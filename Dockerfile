FROM python:3.12

WORKDIR /workspace

COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

CMD ["sleep", "infinity"]
