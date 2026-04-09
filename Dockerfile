FROM ghcr.io/coqui-ai/tts:latest

ENV PYTHONUNBUFFERED=1

COPY requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

COPY server.py /server.py

WORKDIR /workspace

EXPOSE 5002

ENTRYPOINT ["python3", "/server.py"]
