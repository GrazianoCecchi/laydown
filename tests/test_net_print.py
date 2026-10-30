"""
Tests for direct network printing and RIP dispatch (laydown.engine.net_print).
"""

import os
import socket
import sys
import threading
import time
import pytest

from laydown.engine.net_print import (
    PrinterStatus,
    build_pjl_header,
    build_pjl_trailer,
    query_pjl_printer_info,
    send_pdf_lpr,
    send_pdf_raw_socket,
)


def test_engine_net_print_has_no_qt_imports():
    """Ensure engine/net_print.py remains strictly Qt-free."""
    import laydown.engine.net_print as mod
    for name, val in vars(mod).items():
        if hasattr(val, "__module__") and val.__module__:
            assert "PyQt6" not in val.__module__, f"Forbidden Qt import found: {name} ({val.__module__})"
        elif "PyQt6" in str(val):
            assert False, f"Forbidden Qt reference found in {name}: {val}"


def test_build_pjl_header_simplex():
    hdr = build_pjl_header(job_name="Job_A", copies=2, duplex="simplex")
    decoded = hdr.decode("latin1")
    assert "@PJL JOB NAME = \"Job_A\"" in decoded
    assert "@PJL SET QTY = 2" in decoded
    assert "@PJL SET DUPLEX = OFF" in decoded
    assert "@PJL ENTER LANGUAGE = PDF" in decoded


def test_build_pjl_header_duplex_long_and_short():
    hdr_long = build_pjl_header(job_name="Booklet", copies=1, duplex="long_edge")
    dec_long = hdr_long.decode("latin1")
    assert "@PJL SET DUPLEX = ON" in dec_long
    assert "@PJL SET BINDING = LONGEDGE" in dec_long

    hdr_short = build_pjl_header(job_name="Booklet", copies=1, duplex="short_edge")
    dec_short = hdr_short.decode("latin1")
    assert "@PJL SET DUPLEX = ON" in dec_short
    assert "@PJL SET BINDING = SHORTEDGE" in dec_short


def test_build_pjl_trailer():
    trl = build_pjl_trailer(job_name="Job_B").decode("latin1")
    assert "@PJL EOJ NAME = \"Job_B\"" in trl


def test_query_pjl_mock_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def _srv():
        conn, _ = server.accept()
        data = conn.recv(1024)
        if b"@PJL" in data:
            conn.sendall(b'@PJL INFO ID\r\n"RICOH Pro C5300SL"\r\n\x0c')
        conn.close()
        server.close()

    t = threading.Thread(target=_srv, daemon=True)
    t.start()

    status = query_pjl_printer_info("127.0.0.1", port=port, timeout=2.0)
    assert status.is_online is True
    assert "RICOH Pro C5300SL" in status.model
    assert status.display_name == "RICOH Pro C5300SL"


def test_query_pjl_offline():
    status = query_pjl_printer_info("127.0.0.1", port=1, timeout=0.5)
    assert status.is_online is False
    assert status.display_name == "Non raggiungibile"


def test_send_pdf_raw_socket(tmp_path):
    pdf = tmp_path / "test_doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\ntest vector imposition stream\n%%EOF")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    received = bytearray()

    def _srv():
        conn, _ = server.accept()
        while True:
            chunk = conn.recv(1024)
            if not chunk:
                break
            received.extend(chunk)
        conn.close()
        server.close()

    t = threading.Thread(target=_srv, daemon=True)
    t.start()

    progress_log = []

    def _on_progress(sent, total):
        progress_log.append((sent, total))

    sent_bytes = send_pdf_raw_socket(
        str(pdf),
        ip="127.0.0.1",
        port=port,
        job_name="UnitTestJob",
        copies=1,
        duplex="simplex",
        progress_cb=_on_progress,
    )

    t.join(timeout=3.0)
    assert sent_bytes > 0
    assert b"@PJL JOB NAME = \"UnitTestJob\"" in received
    assert b"%PDF-1.7" in received
    assert b"@PJL EOJ" in received
    assert len(progress_log) >= 2


def test_send_pdf_lpr(tmp_path):
    pdf = tmp_path / "test_lpr.pdf"
    pdf.write_bytes(b"%PDF-1.7\nlpr test content\n%%EOF")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    received = bytearray()

    def _srv():
        conn, _ = server.accept()
        # 1. Receive job command
        cmd = conn.recv(64)
        received.extend(cmd)
        conn.sendall(b"\x00")

        # 2. Receive control file cmd
        cmd = conn.recv(64)
        received.extend(cmd)
        conn.sendall(b"\x00")

        # 3. Receive control file data
        cf = conn.recv(256)
        received.extend(cf)
        conn.sendall(b"\x00")

        # 4. Receive data file cmd
        cmd = conn.recv(64)
        received.extend(cmd)
        conn.sendall(b"\x00")

        # 5. Receive data file content + \x00
        while True:
            chunk = conn.recv(1024)
            if not chunk:
                break
            received.extend(chunk)
            if chunk.endswith(b"\x00"):
                conn.sendall(b"\x00")
                break

        conn.close()
        server.close()

    t = threading.Thread(target=_srv, daemon=True)
    t.start()

    sent = send_pdf_lpr(
        str(pdf),
        ip="127.0.0.1",
        port=port,
        queue="SRA3_Custom",
        job_name="LprJob",
    )

    t.join(timeout=3.0)
    assert sent == len(pdf.read_bytes())
    assert b"\x02SRA3_Custom\n" in received
    assert b"%PDF-1.7" in received
