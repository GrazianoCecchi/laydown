"""
Tests for SendToRipDialog and HotFolderManager UI integration (laydown.ui.hot_folder).
"""

import os
import tempfile
from unittest.mock import MagicMock
import fitz
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from laydown.engine.net_print import PrinterStatus
from laydown.ui.hot_folder import HotFolderManager, SendToRipDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sample_pdf():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc = fitz.open()
    page = doc.new_page(width=841.89, height=1190.55)  # A3
    page.insert_text((100, 100), "Sample Imposition")
    doc.save(path)
    doc.close()
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def clean_settings():
    settings = QSettings("Laydown", "Laydown")
    settings.remove("production/test_net_ip")
    settings.remove(HotFolderManager.NET_IP_KEY)
    settings.remove(HotFolderManager.NET_PORT_KEY)
    settings.remove(HotFolderManager.NET_PROTO_KEY)
    settings.remove(HotFolderManager.NET_QUEUE_KEY)
    settings.remove(HotFolderManager.NET_COPIES_KEY)
    settings.remove(HotFolderManager.NET_DUPLEX_KEY)
    yield
    settings.remove("production/test_net_ip")


def test_hot_folder_manager_net_preferences(clean_settings):
    HotFolderManager.set_net_ip("192.168.1.155")
    assert HotFolderManager.get_net_ip() == "192.168.1.155"

    HotFolderManager.set_net_port(9100)
    assert HotFolderManager.get_net_port() == 9100

    HotFolderManager.set_net_proto("raw")
    assert HotFolderManager.get_net_proto() == "raw"

    HotFolderManager.set_net_queue("A3_SRA3_Queue")
    assert HotFolderManager.get_net_queue() == "A3_SRA3_Queue"

    HotFolderManager.set_net_copies(3)
    assert HotFolderManager.get_net_copies() == 3

    HotFolderManager.set_net_duplex("duplex_short")
    assert HotFolderManager.get_net_duplex() == "duplex_short"


def test_send_to_rip_dialog_instantiation(app, sample_pdf):
    dlg = SendToRipDialog(
        pdf_path=sample_pdf,
        project_name="Rivista_A4",
        preset="SRA3",
    )
    assert dlg.windowTitle().startswith("Invia a RIP")
    assert dlg._tabs.count() == 2

    # Initial tab
    dlg._tabs.setCurrentIndex(0)
    assert "Stampante" in dlg._btn_action.text()

    dlg._tabs.setCurrentIndex(1)
    assert "Hot Folder" in dlg._btn_action.text()

    dlg.close()


def test_send_to_rip_dialog_probe_feedback(app, sample_pdf):
    dlg = SendToRipDialog(
        pdf_path=sample_pdf,
        project_name="Test_Job",
    )
    # Simulate probe result
    online_status = PrinterStatus(
        ip="192.168.1.155",
        port=9100,
        is_online=True,
        model="RICOH Pro C5300SL",
        status_display="Pronta",
    )
    dlg._on_probe_result(online_status)
    assert "RICOH Pro C5300SL" in dlg._badge_probe.text()
    assert "Pronta" in dlg._badge_probe.text()

    # Simulate offline probe result
    offline_status = PrinterStatus(
        ip="192.168.1.99",
        port=9100,
        is_online=False,
        error_message="Connessione rifiutata",
    )
    dlg._on_probe_result(offline_status)
    assert "Non in linea" in dlg._badge_probe.text()
    assert "rifiutata" in dlg._badge_probe.text()

    dlg.close()


def test_send_to_rip_dialog_protocol_toggle(app, sample_pdf):
    dlg = SendToRipDialog(
        pdf_path=sample_pdf,
        project_name="Test_Job",
    )
    # Toggle to LPR
    dlg._radio_lpr.setChecked(True)
    assert not dlg._lpr_row_widget.isHidden()
    assert dlg._net_port_spin.value() == 515

    # Toggle back to RAW
    dlg._radio_raw.setChecked(True)
    assert dlg._lpr_row_widget.isHidden()
    assert dlg._net_port_spin.value() == 9100

    dlg.close()
