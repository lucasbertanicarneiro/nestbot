"""Env fake para os testes rodarem sem .env real nem segredo nenhum.

Precisa ser setado antes de qualquer import de src.* -- por isso fica no
nivel do modulo (roda no import do conftest, que o pytest carrega antes de
coletar os arquivos de teste), e nao dentro de uma fixture.
"""
import os

os.environ.setdefault("POSTGRES_PASSWORD", "fake-para-teste")
os.environ.setdefault("GROQ_API_KEY", "fake-para-teste")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake-para-teste")
