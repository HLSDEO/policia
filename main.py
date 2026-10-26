import os

import psycopg2
import psycopg2.extras
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="API de Teste OpenShift")

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "appdb")
DB_USER = os.getenv("DB_USER", "appuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "apppass")


def get_conn():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


class Item(BaseModel):
    nome: str


@app.on_event("startup")
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS itens ("
        "id SERIAL PRIMARY KEY, nome TEXT NOT NULL);"
    )
    conn.commit()
    cur.close()
    conn.close()


@app.get("/")
def root():
    return {"message": "Hello from FastAPI on OpenShift!"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db")
def db_check():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT version();")
    version = cur.fetchone()[0]
    cur.close()
    conn.close()
    return {"postgres": version}


@app.get("/items")
def list_items():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, nome FROM itens ORDER BY id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"itens": rows}


@app.post("/items")
def create_item(item: Item):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "INSERT INTO itens (nome) VALUES (%s) RETURNING id, nome;",
        (item.nome,),
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return {"item": dict(row)}
