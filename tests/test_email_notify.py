import time
import threading
import asyncio

from scripts import run_and_notify


async def _smtp_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, out_list: list):
    writer.write(b"220 localhost SimpleSMTP\r\n")
    await writer.drain()
    mailfrom = None
    rcpttos = []
    data_lines = []
    in_data = False
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode(errors="ignore").rstrip("\r\n")
            if in_data:
                if text == ".":
                    in_data = False
                    out_list.append("\n".join(data_lines))
                    data_lines.clear()
                    writer.write(b"250 OK\r\n")
                    await writer.drain()
                else:
                    data_lines.append(text)
                continue

            upper = text.upper()
            if upper.startswith("EHLO") or upper.startswith("HELO"):
                writer.write(b"250-localhost Hello\r\n250 OK\r\n")
            elif upper.startswith("MAIL FROM"):
                mailfrom = text[10:].strip()
                writer.write(b"250 OK\r\n")
            elif upper.startswith("RCPT TO"):
                rcpttos.append(text[8:].strip())
                writer.write(b"250 OK\r\n")
            elif upper == "DATA":
                writer.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                in_data = True
            elif upper in ("QUIT", "RSET"):
                writer.write(b"221 Bye\r\n")
                await writer.drain()
                break
            else:
                writer.write(b"250 OK\r\n")
            await writer.drain()
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def _start_async_smtp(host: str, port: int, out_list: list):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _runner():
        server = await asyncio.start_server(lambda r, w: _smtp_handler(r, w, out_list), host, port)
        try:
            await server.serve_forever()
        finally:
            server.close()
            await server.wait_closed()

    try:
        loop.run_until_complete(_runner())
    finally:
        loop.close()


def test_send_email_integration(monkeypatch):
    host = "127.0.0.1"
    port = 1025
    received = []

    thread = threading.Thread(target=_start_async_smtp, args=(host, port, received), daemon=True)
    thread.start()
    time.sleep(0.1)

    monkeypatch.setenv("SMTP_HOST", host)
    monkeypatch.setenv("SMTP_PORT", str(port))
    monkeypatch.setenv("EMAIL_TO", "recipient@example.com")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)

    monkeypatch.setattr(run_and_notify, "run_command", lambda cmd: {"command": " ".join(cmd), "exit_code": 0, "stdout": "ok", "stderr": ""})

    rc = run_and_notify.main(["--date", "2026-05-04"])
    assert rc == 0

    # give the server a moment to process
    time.sleep(0.2)

    # Stop the server by connecting and sending QUIT
    try:
        import socket

        with socket.create_connection((host, port), timeout=1) as s:
            s.recv(1024)
            s.sendall(b"QUIT\r\n")
    except Exception:
        pass

    thread.join(timeout=2)
    assert len(received) >= 1, f"expected at least 1 message, got {len(received)}"
    data = received[0]
    if isinstance(data, bytes):
        assert b"Dispatches publish result" in data
    else:
        assert "Dispatches publish result" in data
