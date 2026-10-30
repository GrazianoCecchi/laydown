"""
Direct Network Printing & RIP Dispatch (Qt-free).

Provides raw socket and LPR protocol dispatch to production digital presses
and RIP controllers (such as Ricoh Pro C5300SL, Canon imagePRESS, Xerox, Konica Minolta)
that support PDF Direct Print and PJL (Printer Job Language).

This module contains NO Qt dependencies and is 100% testable headless.
"""

from dataclasses import dataclass
import os
import socket
import time
from typing import Callable, Optional, Tuple


@dataclass(frozen=True)
class PrinterStatus:
    """Status information obtained from printer network probe."""
    ip: str
    port: int
    is_online: bool
    model: str = ""
    status_display: str = ""
    error_message: str = ""

    @property
    def display_name(self) -> str:
        if self.model:
            return self.model
        if self.is_online:
            return f"Stampante a {self.ip}:{self.port}"
        return "Non raggiungibile"


def query_pjl_printer_info(ip: str, port: int = 9100, timeout: float = 2.5) -> PrinterStatus:
    """
    Probe the printer over TCP port 9100 using standard PJL (Printer Job Language) commands.
    Returns PrinterStatus with parsed model identity and online status.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        # Query model ID and status display via PJL
        probe_cmd = (
            b"\x1b%-12345X@PJL \r\n"
            b"@PJL INFO ID\r\n"
            b"@PJL INFO STATUS\r\n"
            b"\x1b%-12345X"
        )
        sock.sendall(probe_cmd)

        response = b""
        start_t = time.time()
        while time.time() - start_t < timeout:
            try:
                chunk = sock.recv(2048)
                if not chunk:
                    break
                response += chunk
                if b"\x0c" in response or len(response) > 512:
                    break
            except socket.timeout:
                break

        model = ""
        status_disp = ""
        text = response.decode("latin1", errors="replace")

        # Parse PJL INFO ID response (e.g. '"RICOH Pro C5300SL"')
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            line_str = line.strip()
            if "INFO ID" in line_str:
                for next_l in lines[idx + 1:idx + 4]:
                    clean = next_l.strip().strip('"')
                    if clean and not clean.startswith("@PJL"):
                        model = clean
                        break
            if "DISPLAY=" in line_str:
                status_disp = line_str.split("DISPLAY=", 1)[1].strip().strip('"')

        return PrinterStatus(
            ip=ip,
            port=port,
            is_online=True,
            model=model or "Stampante RAW/PJL",
            status_display=status_disp or "Pronta",
        )
    except socket.timeout:
        return PrinterStatus(
            ip=ip,
            port=port,
            is_online=False,
            error_message="Timeout: la stampante non risponde entro il tempo limite.",
        )
    except ConnectionRefusedError:
        return PrinterStatus(
            ip=ip,
            port=port,
            is_online=False,
            error_message=f"Connessione rifiutata sulla porta {port}.",
        )
    except Exception as exc:
        return PrinterStatus(
            ip=ip,
            port=port,
            is_online=False,
            error_message=str(exc),
        )
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def build_pjl_header(
    job_name: str = "Laydown_Print",
    copies: int = 1,
    duplex: Optional[str] = None,
    binding: Optional[str] = None,
) -> bytes:
    """
    Construct standard PJL header for direct PDF printing.
    Controls copies, duplex, binding and language auto-switch to PDF.
    """
    clean_job = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in job_name).strip()
    if not clean_job:
        clean_job = "Laydown_Print"

    lines = [
        "\x1b%-12345X@PJL",
        f'@PJL JOB NAME = "{clean_job}"',
        f"@PJL SET QTY = {max(1, int(copies))}",
    ]

    # Duplex settings
    if duplex in ("duplex", "long_edge", "short_edge", True):
        lines.append("@PJL SET DUPLEX = ON")
        if binding == "short_edge" or duplex == "short_edge":
            lines.append("@PJL SET BINDING = SHORTEDGE")
        else:
            lines.append("@PJL SET BINDING = LONGEDGE")
    elif duplex in ("simplex", False):
        lines.append("@PJL SET DUPLEX = OFF")

    lines.append("@PJL ENTER LANGUAGE = PDF")
    header_str = "\r\n".join(lines) + "\r\n"
    return header_str.encode("latin1")


def build_pjl_trailer(job_name: str = "Laydown_Print") -> bytes:
    """Construct PJL End-Of-Job footer."""
    clean_job = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in job_name).strip()
    trailer_str = f"\x1b%-12345X@PJL EOJ NAME = \"{clean_job}\"\r\n\x1b%-12345X\r\n"
    return trailer_str.encode("latin1")


def send_pdf_raw_socket(
    pdf_path: str,
    ip: str,
    port: int = 9100,
    job_name: str = "Laydown_Print",
    copies: int = 1,
    duplex: Optional[str] = None,
    binding: Optional[str] = None,
    use_pjl: bool = True,
    chunk_size: int = 65536,
    timeout: float = 10.0,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> int:
    """
    Send a PDF directly to printer via RAW socket (Port 9100), optionally wrapped in PJL.
    Streams in chunks to avoid high memory consumption.
    Returns total bytes transmitted.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"File PDF non trovato: {pdf_path}")

    file_size = os.path.getsize(pdf_path)
    if file_size <= 0:
        raise ValueError(f"File PDF vuoto: {pdf_path}")

    header = build_pjl_header(job_name, copies, duplex, binding) if use_pjl else b""
    trailer = build_pjl_trailer(job_name) if use_pjl else b""
    total_bytes = len(header) + file_size + len(trailer)

    sock = None
    bytes_sent = 0

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        # Send PJL header
        if header:
            sock.sendall(header)
            bytes_sent += len(header)
            if progress_cb:
                progress_cb(bytes_sent, total_bytes)

        # Stream PDF file content
        with open(pdf_path, "rb") as f:
            while True:
                if cancel_check and cancel_check():
                    raise InterruptedError("Invio annullato dall'utente.")

                chunk = f.read(chunk_size)
                if not chunk:
                    break

                sock.sendall(chunk)
                bytes_sent += len(chunk)
                if progress_cb:
                    progress_cb(bytes_sent, total_bytes)

        # Send PJL trailer
        if trailer:
            sock.sendall(trailer)
            bytes_sent += len(trailer)
            if progress_cb:
                progress_cb(bytes_sent, total_bytes)

        return bytes_sent

    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def send_pdf_lpr(
    pdf_path: str,
    ip: str,
    port: int = 515,
    queue: str = "lp",
    job_name: str = "Laydown_Print",
    chunk_size: int = 65536,
    timeout: float = 10.0,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> int:
    """
    Send a PDF directly to printer via LPR / LPD (RFC 1179, Port 515).
    Allows targeting specific Ricoh Virtual Printers or queues (e.g. 'lp', 'raw').
    Returns total bytes transmitted.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"File PDF non trovato: {pdf_path}")

    file_size = os.path.getsize(pdf_path)
    if file_size <= 0:
        raise ValueError(f"File PDF vuoto: {pdf_path}")

    queue_name = queue.strip() or "lp"
    host_name = "laydown"
    user_name = "operator"

    # RFC 1179 Control File Content
    control_file = (
        f"H{host_name}\n"
        f"P{user_name}\n"
        f"J{job_name}\n"
        f"ldfA001{host_name}\n"
        f"UdfA001{host_name}\n"
        f"N{job_name}\n"
    ).encode("latin1")

    sock = None
    bytes_sent = 0

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        # 1. Receive print job command: \x02 + queue + \n
        sock.sendall(f"\x02{queue_name}\n".encode("latin1"))
        ack = sock.recv(1)
        if ack != b"\x00":
            raise RuntimeError(f"Coda LPR '{queue_name}' rifiutata dalla stampante (codice {ack!r}).")

        # 2. Send control file command: \x02 + length + name + \n
        cf_name = f"cfA001{host_name}"
        sock.sendall(f"\x02{len(control_file)} {cf_name}\n".encode("latin1"))
        ack = sock.recv(1)
        if ack != b"\x00":
            raise RuntimeError(f"Comando file di controllo LPR rifiutato (codice {ack!r}).")

        # 3. Send control file data + \x00
        sock.sendall(control_file + b"\x00")
        ack = sock.recv(1)
        if ack != b"\x00":
            raise RuntimeError(f"File di controllo LPR rifiutato (codice {ack!r}).")

        # 4. Send data file command: \x03 + length + name + \n
        df_name = f"dfA001{host_name}"
        sock.sendall(f"\x03{file_size} {df_name}\n".encode("latin1"))
        ack = sock.recv(1)
        if ack != b"\x00":
            raise RuntimeError(f"Comando dati LPR rifiutato (codice {ack!r}).")

        # 5. Stream data file
        with open(pdf_path, "rb") as f:
            while True:
                if cancel_check and cancel_check():
                    raise InterruptedError("Invio annullato dall'utente.")

                chunk = f.read(chunk_size)
                if not chunk:
                    break

                sock.sendall(chunk)
                bytes_sent += len(chunk)
                if progress_cb:
                    progress_cb(bytes_sent, file_size)

        # 6. End of data file marker \x00
        sock.sendall(b"\x00")
        ack = sock.recv(1)
        if ack != b"\x00":
            raise RuntimeError(f"File dati LPR non riconosciuto con successo (codice {ack!r}).")

        return bytes_sent

    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
