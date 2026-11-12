FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backoffice backoffice
COPY synth synth
COPY eval eval
COPY scripts scripts
RUN useradd -m app && chown -R app /app
USER app
ENV BACKOFFICE_VAR=/data/var BACKOFFICE_DATA=/data/companies
EXPOSE 8080
CMD ["uvicorn", "backoffice.api:app", "--host", "0.0.0.0", "--port", "8080"]
