ARG BUILD_FROM=ghcr.io/hassio-addons/base-python:17.2.0
FROM ${BUILD_FROM}

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY run.sh /run.sh
COPY ue_bridge.py /app/ue_bridge.py

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
