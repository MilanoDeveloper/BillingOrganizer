from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
import hashlib
import os
import re

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf import PdfReader
import psycopg
from psycopg.errors import UniqueViolation
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


class TransactionUpdate(BaseModel):
    descricao: str = Field(min_length=1, max_length=255)
    categoria: str = Field(min_length=1, max_length=100)


class CategoryCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=100)


CATEGORY_RULES = {
    "Investimentos": ("APLICACAO COFRINHOS", "INVESTIMENTO", "APLICACAO"),
    "Contas fixas": (
        "BOLETO BANCO VOLKSWAGEN",
        "BOLETO CONDOMINIO",
        "COMPANHIA DE GAS",
        "COMGAS",
        "PIX QRS TELEFONICA",
    ),
}


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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS financeiro.categorias (
                    id SERIAL PRIMARY KEY,
                    usuario_id INT NOT NULL REFERENCES financeiro.usuarios(id) ON DELETE CASCADE,
                    nome VARCHAR(100) NOT NULL,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uk_categoria_usuario_nome UNIQUE (usuario_id, nome)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS financeiro.importacoes (
                    id SERIAL PRIMARY KEY,
                    usuario_id INT NOT NULL REFERENCES financeiro.usuarios(id) ON DELETE CASCADE,
                    nome_arquivo VARCHAR(255) NOT NULL,
                    hash_arquivo VARCHAR(64) NOT NULL,
                    importado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uk_importacao_usuario_hash UNIQUE (usuario_id, hash_arquivo)
                )
                """
            )
            cursor.executemany(
                """
                INSERT INTO financeiro.categorias (usuario_id, nome)
                VALUES (%s, %s)
                ON CONFLICT (usuario_id, nome) DO NOTHING
                """,
                [(DEFAULT_USER_ID, name) for name in ("Outros", "Moradia", "Alimentação", "Transporte", "Saúde", "Lazer", "Investimentos", "Contas fixas")],
            )


def parse_brazilian_amount(value: str) -> Decimal:
    normalized = value.strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError(f"Valor inválido: {value}") from error


def categorize_description(description: str) -> str:
    normalized = description.upper()
    for category, keywords in CATEGORY_RULES.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return "Outros"


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
                "categoria": categorize_description(description),
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


@app.get("/api/categorias")
def list_categories() -> list[dict]:
    with connection() as conn:
        return conn.execute(
            "SELECT id, nome FROM financeiro.categorias WHERE usuario_id = %s ORDER BY nome",
            (DEFAULT_USER_ID,),
        ).fetchall()


@app.post("/api/categorias", status_code=201)
def create_category(category: CategoryCreate) -> dict:
    name = category.nome.strip()
    try:
        with connection() as conn:
            return conn.execute(
                "INSERT INTO financeiro.categorias (usuario_id, nome) VALUES (%s, %s) RETURNING id, nome",
                (DEFAULT_USER_ID, name),
            ).fetchone()
    except UniqueViolation as error:
        raise HTTPException(status_code=409, detail="Essa categoria já existe.") from error


@app.get("/api/transacoes")
def list_transactions(
    data_inicio: date | None = Query(default=None),
    data_fim: date | None = Query(default=None),
) -> list[dict]:
    if data_inicio and data_fim and data_inicio > data_fim:
        raise HTTPException(status_code=400, detail="A data inicial deve ser anterior à data final.")
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, data, descricao, valor, origem, categoria, metodo_pagamento, tipo_entrada
            FROM financeiro.transacoes
            WHERE usuario_id = %s
              AND (%s::date IS NULL OR data >= %s::date)
              AND (%s::date IS NULL OR data <= %s::date)
            ORDER BY data DESC, id DESC
            LIMIT 100
            """,
            (DEFAULT_USER_ID, data_inicio, data_inicio, data_fim, data_fim),
        ).fetchall()
    return rows


@app.get("/api/resumo")
def summary(
    data_inicio: date | None = Query(default=None),
    data_fim: date | None = Query(default=None),
) -> dict:
    if data_inicio and data_fim and data_inicio > data_fim:
        raise HTTPException(status_code=400, detail="A data inicial deve ser anterior à data final.")
    period_params = (DEFAULT_USER_ID, data_inicio, data_inicio, data_fim, data_fim)
    with connection() as conn:
        totals = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN valor < 0 THEN ABS(valor) ELSE 0 END), 0) AS gastos,
              COALESCE(SUM(CASE WHEN valor > 0 THEN valor ELSE 0 END), 0) AS entradas,
              COUNT(*) AS quantidade
            FROM financeiro.transacoes
                        WHERE usuario_id = %s
                              AND (%s::date IS NULL OR data >= %s::date)
                              AND (%s::date IS NULL OR data <= %s::date)
            """,
                        period_params,
        ).fetchone()
        categories = conn.execute(
            """
            SELECT categoria AS nome, COALESCE(SUM(ABS(valor)), 0) AS total
            FROM financeiro.transacoes
                        WHERE usuario_id = %s AND valor < 0
                              AND (%s::date IS NULL OR data >= %s::date)
                              AND (%s::date IS NULL OR data <= %s::date)
            GROUP BY categoria
            ORDER BY total DESC
            """,
                        period_params,
        ).fetchall()
        grouped = conn.execute(
            """
            SELECT descricao AS nome, COALESCE(SUM(ABS(valor)), 0) AS total, COUNT(*) AS ocorrencias
            FROM financeiro.transacoes
                        WHERE usuario_id = %s AND valor < 0
                              AND (%s::date IS NULL OR data >= %s::date)
                              AND (%s::date IS NULL OR data <= %s::date)
            GROUP BY descricao
            ORDER BY total DESC
            LIMIT 8
            """,
                        (DEFAULT_USER_ID, data_inicio, data_inicio, data_fim, data_fim),
        ).fetchall()
    return {"gastos": totals["gastos"], "entradas": totals["entradas"], "quantidade": totals["quantidade"], "categorias": categories, "maiores_gastos": grouped}


@app.patch("/api/transacoes/{transaction_id}")
def update_transaction(transaction_id: int, transaction: TransactionUpdate) -> dict:
    with connection() as conn:
        result = conn.execute(
            """
            UPDATE financeiro.transacoes
            SET descricao = %s, categoria = %s
            WHERE id = %s AND usuario_id = %s
            RETURNING id, data, descricao, valor, origem, categoria, metodo_pagamento, tipo_entrada
            """,
            (transaction.descricao.strip(), transaction.categoria.strip(), transaction_id, DEFAULT_USER_ID),
        ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Transação não encontrada.")
    return result


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
    file_hash = hashlib.sha256(content).hexdigest()
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO financeiro.importacoes (usuario_id, nome_arquivo, hash_arquivo) VALUES (%s, %s, %s)",
                (DEFAULT_USER_ID, file.filename[:255], file_hash),
            )
            if transactions:
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
            imported = len(transactions)
    except UniqueViolation as error:
        raise HTTPException(status_code=409, detail="Este arquivo já foi importado anteriormente.") from error
    return {"message": f"{imported} lançamentos importados.", "importados": imported}
