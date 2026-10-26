import os

import psycopg2
from fastapi import FastAPI

app = FastAPI(title="API de Teste OpenShift")

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "appdb")
DB_USER = os.getenv("DB_USER", "appuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "apppass")


@app.get("/")
def root():
    return {"message": "Hello from FastAPI on OpenShift!"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db")
def db_check():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    cur = conn.cursor()
    cur.execute("SELECT version();")
    version = cur.fetchone()[0]
    cur.close()
    conn.close()
    return {"postgres": version}
