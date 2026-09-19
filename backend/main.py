from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
import os
import re

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf import PdfReader
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:abc123@localhost:5432/postgres",
)
DEFAULT_USER_ID = 1

app = FastAPI(title="Billing Organizer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ManualTransaction(BaseModel):
    descricao: str = Field(min_length=1, max_length=255)
    valor: Decimal
    data: date = Field(default_factory=date.today)
    categoria: str = Field(default="Outros", max_length=100)
    metodo_pagamento: str | None = Field(default=None, max_length=50)


def connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_default_user() -> None:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO financeiro.usuarios (id, nome, email, senha_hash)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (DEFAULT_USER_ID, "Usuário principal", "local@billing.organizer", "local"),
            )
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('financeiro.usuarios', 'id'), GREATEST((SELECT MAX(id) FROM financeiro.usuarios), 1))"
            )


def parse_brazilian_amount(value: str) -> Decimal:
    normalized = value.strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError(f"Valor inválido: {value}") from error


def extract_pdf_text(content: bytes, filename: str) -> str:
    if filename.lower().endswith(".txt"):
        return content.decode("utf-8", errors="replace")
    try:
        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise HTTPException(status_code=400, detail="Não foi possível ler o PDF.") from error


def parse_statement(text: str, origin: str) -> list[dict]:
    transactions = []
    line_pattern = re.compile(
        r"^(?P<day>\d{2}/\d{2}/\d{4})\s+(?P<description>.+?)\s+(?P<amount>-?\d{1,3}(?:\.\d{3})*,\d{2})$"
    )
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        match = line_pattern.match(line)
        if not match or "SALDO DO DIA" in match.group("description").upper():
            continue
        description = match.group("description").strip()
        if not description:
            continue
        transactions.append(
            {
                "usuario_id": DEFAULT_USER_ID,
                "data": datetime.strptime(match.group("day"), "%d/%m/%Y").date(),
                "descricao": description[:255],
                "valor": parse_brazilian_amount(match.group("amount")),
                "origem": origin[:100],
                "categoria": "Outros",
                "metodo_pagamento": "Extrato",
                "tipo_entrada": "PDF",
            }
        )
    return transactions


def insert_transactions(transactions: list[dict]) -> int:
    if not transactions:
        return 0
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO financeiro.transacoes
                (usuario_id, data, descricao, valor, origem, categoria, metodo_pagamento, tipo_entrada)
                VALUES (%(usuario_id)s, %(data)s, %(descricao)s, %(valor)s, %(origem)s,
                        %(categoria)s, %(metodo_pagamento)s, %(tipo_entrada)s)
                """,
                transactions,
            )
    return len(transactions)


@app.on_event("startup")
def startup() -> None:
    ensure_default_user()


@app.get("/api/health")
def health() -> dict:
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/transacoes")
def list_transactions() -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, data, descricao, valor, origem, categoria, metodo_pagamento, tipo_entrada
            FROM financeiro.transacoes
            WHERE usuario_id = %s
            ORDER BY data DESC, id DESC
            LIMIT 100
            """,
            (DEFAULT_USER_ID,),
        ).fetchall()
    return rows


@app.get("/api/resumo")
def summary() -> dict:
    with connection() as conn:
        totals = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN valor < 0 THEN ABS(valor) ELSE 0 END), 0) AS gastos,
              COALESCE(SUM(CASE WHEN valor > 0 THEN valor ELSE 0 END), 0) AS entradas,
              COUNT(*) AS quantidade
            FROM financeiro.transacoes
            WHERE usuario_id = %s
            """,
            (DEFAULT_USER_ID,),
        ).fetchone()
        categories = conn.execute(
            """
            SELECT categoria AS nome, COALESCE(SUM(ABS(valor)), 0) AS total
            FROM financeiro.transacoes
            WHERE usuario_id = %s AND valor < 0
            GROUP BY categoria
            ORDER BY total DESC
            """,
            (DEFAULT_USER_ID,),
        ).fetchall()
        grouped = conn.execute(
            """
            SELECT descricao AS nome, COALESCE(SUM(ABS(valor)), 0) AS total, COUNT(*) AS ocorrencias
            FROM financeiro.transacoes
            WHERE usuario_id = %s AND valor < 0
            GROUP BY descricao
            ORDER BY total DESC
            LIMIT 8
            """,
            (DEFAULT_USER_ID,),
        ).fetchall()
    return {"gastos": totals["gastos"], "entradas": totals["entradas"], "quantidade": totals["quantidade"], "categorias": categories, "maiores_gastos": grouped}


@app.post("/api/transacoes/manual", status_code=201)
def create_manual(transaction: ManualTransaction) -> dict:
    values = {
        "usuario_id": DEFAULT_USER_ID,
        "data": transaction.data,
        "descricao": transaction.descricao,
        "valor": -abs(transaction.valor),
        "origem": "Lançamento manual",
        "categoria": transaction.categoria,
        "metodo_pagamento": transaction.metodo_pagamento,
        "tipo_entrada": "MANUAL",
    }
    insert_transactions([values])
    return {"message": "Conta adicionada com sucesso."}


@app.post("/api/importar", status_code=201)
async def import_statement(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith((".pdf", ".txt")):
        raise HTTPException(status_code=400, detail="Envie um arquivo PDF ou TXT.")
    content = await file.read()
    transactions = parse_statement(extract_pdf_text(content, file.filename), file.filename)
    imported = insert_transactions(transactions)
    return {"message": f"{imported} lançamentos importados.", "importados": imported}
