from automata.tm.dtm import DTM
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi_mail import ConnectionConfig, MessageSchema, MessageType, FastMail
from sqlalchemy.orm import Session
from typing import List
import pika

from sql_app import crud, models, schemas
from sql_app.database import engine, SessionLocal
from util.email_body import EmailSchema

from prometheus_fastapi_instrumentator import Instrumentator

models.Base.metadata.create_all(bind=engine)

conf = ConnectionConfig(
    MAIL_USERNAME="cf55943d77a85f",
    MAIL_PASSWORD="6bdfc1d277a534",
    MAIL_FROM="from@example.com",
    MAIL_PORT=465,
    MAIL_SERVER="sandbox.smtp.mailtrap.io",
    MAIL_STARTTLS=False,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True
)

app = FastAPI()

Instrumentator().instrument(app).expose(app)


# Patter Singleton
# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/get_history/{id}")
async def get_history(id: int, db: Session = Depends(get_db)):
    history = crud.get_history(db=db, id=id)
    if history is None:
        return {
            "code": "404",
            "msg": "not found"
        }
    return history


@app.get("/get_all_history")
async def get_all_history(db: Session = Depends(get_db)):
    history = crud.get_all_history(db=db)
    return history

def process_dtm_batch(dtms_info):
    results = []
    for info in dtms_info:
        states = set(info.get("states", []))
        if len(states) == 0:
            return {
            "code": "400",
            "msg": "states cannot be empty"
            }
        input_symbols = set(info.get("input_symbols", []))
        if len(input_symbols) == 0:
            return {
            "code": "400",
            "msg": "input_symbols cannot be empty"
            }
        tape_symbols = set(info.get("tape_symbols", []))
        if len(tape_symbols) == 0:
            return {
            "code": "400",
            "msg": "tape_symbols cannot be empty"
            }
        initial_state = info.get("initial_state", "")
        if initial_state == "":
            return {
            "code": "400",
            "msg": "initial_state cannot be empty"
            }
        blank_symbol = info.get("blank_symbol", "")
        if blank_symbol == "":
            return {
            "code": "400",
            "msg": "blank_symbol cannot be empty"
            }
        final_states = set(info.get("final_states", []))
        if len(final_states) == 0:
            return {
            "code": "400",
            "msg": "final_states cannot be empty"
            }
        transitions = dict(info.get("transitions", {}))
        if len(transitions) == 0:
            return {
            "code": "400",
            "msg": "transitions cannot be empty"
            }
        input = info.get("input", "")
        if input == "":
            return {
            "code": "400",
            "msg": "input cannot be empty"
            }

        dtm = DTM(
        states=states,
        input_symbols=input_symbols,
        tape_symbols=tape_symbols,
        transitions=transitions,
        initial_state=initial_state,
        blank_symbol=blank_symbol,
        final_states=final_states,
        )

        input_str = info.get("input", "")
        if input_str == "":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="input cannot be empty")

        if dtm.accepts_input(input_str):
            results.append("accepted")
        else:
            results.append("rejected")
    return results

@app.post("/dtm_batch/")
async def dtm_batch(dtms_info: List[dict], db: Session = Depends(get_db)):
    try:
        results = process_dtm_batch(dtms_info)

        send_to_rabbitmq(str(results))

        batch_results = []
        for i, result in enumerate(results):
            dtm_info = dtms_info[i]
            history = schemas.History(query=str(dtm_info), result=result)
            crud.create_history(db=db, history=history)

            email_schema = EmailSchema(email=["to@example.com"])
            await simple_send(email_schema, result=result, configuration=str(dtm_info))

            batch_results.append({"result": result, "configuration": dtm_info})

        return {"batch_results": batch_results}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

async def simple_send(email: EmailSchema, result: str, configuration: str):
    html = """
    <p>Thanks for using Fastapi-mail</p>
    <p> The result is: """ + result + """</p>
    <p> We have used this configuration: """ + configuration + """</p>
    """
    message = MessageSchema(
        subject="Fastapi-Mail module",
        recipients=email.dict().get("email"),
        body=html,
        subtype=MessageType.html)

    fm = FastMail(conf)
    await fm.send_message(message)
    return "OK"

def send_to_rabbitmq(result):
    connection = None
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
        channel = connection.channel()
        channel.queue_declare(queue='result_queue')
        channel.basic_publish(exchange='',
                              routing_key='result_queue',
                              body=result)
        print(" [x] Sent:", result)
    except pika.exceptions.AMQPError as e:
        print("Error while sending to RabbitMQ:", str(e))
    except Exception as e:
        print("Error:", str(e))
    finally:
        if connection and not connection.is_closed:
            connection.close()
