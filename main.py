from fastapi import FastAPI, Response
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from logger import logger
from pydantic import BaseModel
import serial
import re
import requests
import random
import threading
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# DTO
class ReleaseDTO(BaseModel):
    idLoan: int
    idActive: int

# Serial connection
backend_url = 'http://localhost:8080'
serial_conn = None
config_port = 'COM5'
config_baudrate = 9600

try:
    serial_conn = serial.Serial(
        port = config_port,
        baudrate= config_baudrate,
         bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE
    )
    logger.info(f'connected to COM port {config_port} with {config_baudrate} bauds')
except serial.SerialException as e:
    logger.error(f'Error openning COM port {config_port}: {e}')
    exit()
 
# Variables from serial reading
scheduler = AsyncIOScheduler()
active_list = []
last_active_arrived = None
lock = threading.Lock()

PATTERN1 = r'^C\d{2}$'
PATTERN2= r"^'C\d{2}'(, 'C\d{2}')*$"

# Serial reading
#     
def process_response(response):
    global active_list
    global last_active_arrived
    if re.match(PATTERN2, response):
            logger.info('COM delivered actives list')
            arr = response.replace(" ", "").split(",")
            with lock:
                active_list = list(map(lambda e: {
                    "id" : int(e.replace("'", "")[1:]),
                    "internalCode": e,
                    "available": True
                }, arr))
    elif re.match(PATTERN1, response):
        with lock:
            if last_active_arrived != response:
                last_active_arrived = response
                logger.info(f"COM detected a return: {response}")
                complete_loan(response)                
            else:
                logger.info('Pendiente de entrega')
                serial_conn.write("RETURN_OK".encode('utf-8'))
    else:
            logger.info(f'Message not recognized: {response}')


def read_from_serial():
    if serial_conn.isOpen():
        if serial_conn.in_waiting > 0:
            return serial_conn.readline().decode('utf-8').strip()
    else:
        raise Exception('COM port is closed :()')
    

def complete_loan(response):
    # Print the return confirmation
    serial_conn.write("RETURN_OK".encode('utf-8'))

    logger.info('Making request to return the active associated with the last loan')
    url = f'{backend_url}/prestacarro/returns/returnLastActive'
    params = {'internalCode': response}

    response = requests.get(url, params= params)    
    logger.info(f'Return completition got an status: {response.status_code}')


async def ask_availables_background():
    logger.info('ask_availables_background triggered')
    serial_conn.write("GET_AVAILABLE_ACTIVES\n".encode('utf-8'))


async def check_returns_background():
    logger.info('check_returns_background triggered')
    while True:
        response = read_from_serial()
        if response:
            logger.info(f'Read from serial: {response}')
            process_response(response)
        # wait
        await asyncio.sleep(1)


# Fast API APP
app = FastAPI()

origins = ["*"] 

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# FAST API Life cycle functions
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(check_returns_background())
    scheduler.add_job(
        ask_availables_background,
        "interval",
        seconds=12,
        id="ask_availables_background",
        replace_existing=True,
    )
    scheduler.start()


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown()


@app.post("/release")
async def release(content: ReleaseDTO):
    try:
        str_fmtr = f'C0{content.idActive}\n'
        logger.info(f'Liberando silla {str_fmtr}')
        serial_conn.write(str_fmtr.encode('utf-8'))
        logger.info('Sent signal to release')
        return {"message": "Message sent to COM"}
    except Exception as e:
        logger.error(f'Communication error with COM: {e}')
        return Response(content=str(e), status_code=500)


@app.post("/actives")
async def actives():
    try:
        with lock:
            return active_list
    except Exception as e:
        logger.error(f'Error de comunicación con el puerto COM: {e}')
        return Response(content=str(e), status_code=500)


@app.post("/write")
async def write(text: str):
    try:
        return Response(content="Mensaje enviado", status_code=200)
    except Exception as e:
        logger.error(f'Error de comunicación con el puerto COM: {e}')
        return Response(content=str(e), status_code=500)
    

@app.post("/getRandomAvailable")
async def activate():
    try:
        with lock:
            if active_list:
                return random.choice(active_list)
            return Response(status_code=204)
    except Exception as e:
        logger.error(f'Error de comunicación con el puerto COM: {e}')
        return Response(content=str(e), status_code=500)


# API Actions
@app.get("/health")
def health():
    return {"status": "200"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9004)