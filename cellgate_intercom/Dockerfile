FROM ghcr.io/tech7fox/asterisk-hass-addon:6.4.0
RUN python3 -m pip install --no-cache-dir --break-system-packages aiohttp==3.14.5
COPY bridge /opt/cellgate/bridge
WORKDIR /opt/cellgate
ENTRYPOINT ["python3", "-m", "bridge"]
