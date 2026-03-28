FROM --platform=linux/amd64 ghcr.io/coqui-ai/tts:latest

COPY requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

COPY server.py /server.py

WORKDIR /workspace

EXPOSE 5002

ENTRYPOINT ["python3", "/server.py"]
