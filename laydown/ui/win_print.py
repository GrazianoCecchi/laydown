"""
Windows Native Print Driver & Direct GDI Spooler Integration for Laydown.

Provides direct access to Windows Winspool and GDI32 subsystems to:
1. Directly open manufacturer-native printer driver preferences dialogs
   (DocumentPropertiesW with DM_IN_PROMPT), bypassing the generic Windows preview dialog.
2. Forcefully synchronize paper size (A3, A4, SRA3, etc.), orientation, duplex, and copies
   into the driver's private DEVMODE structure.
3. Spool print pages directly to the native Windows GDI Device Context (CreateDCW, StartDocW,
   StartPage, StretchDIBits, EndPage, EndDoc) using PyMuPDF-rendered rasters, preventing
   driver auto-downscaling to A4.
"""

import contextlib
import ctypes
import os
import sys
from typing import Callable, List, Optional, Tuple

if sys.platform == "win32":
    from ctypes import wintypes
    try:
        winspool = ctypes.WinDLL("winspool.drv")
        gdi32 = ctypes.WinDLL("gdi32.dll")
        user32 = ctypes.windll.user32
        ole32 = ctypes.WinDLL("ole32.dll")
    except Exception:
        winspool = None
        gdi32 = None
        user32 = None
        ole32 = None
else:
    wintypes = None
    winspool = None
    gdi32 = None
    user32 = None
    ole32 = None

import fitz
from PyQt6.QtCore import QByteArray, QRectF
from PyQt6.QtGui import QImage, QPageLayout, QPageSize, QPainter
from PyQt6.QtPrintSupport import QPrintEngine, QPrinter, QPrinterInfo
from PyQt6.QtSvg import QSvgRenderer


@contextlib.contextmanager
def com_initialized():
    """Ensure COM is initialized on the calling thread for print spooler drivers."""
    if ole32 is None:
        yield
        return
    co_init = False
    try:
        # 0x0 = S_OK, 0x1 = S_FALSE (already initialized)
        hr = ole32.CoInitialize(None)
        co_init = (hr in (0, 1))
    except Exception:
        pass
    try:
        yield
    finally:
        if co_init:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


# ── Windows API Constants ───────────────────────────────────

DM_UPDATE = 1
DM_COPY = 2        # DM_OUT_BUFFER
DM_PROMPT = 4      # DM_IN_PROMPT
DM_MODIFY = 8      # DM_IN_BUFFER
DM_IN_AND_OUT = DM_MODIFY | DM_COPY          # 10
DM_PROMPT_AND_BUFFER = DM_PROMPT | DM_MODIFY | DM_COPY  # 14

IDOK = 1
IDCANCEL = 2

# dmFields bitmasks
DM_ORIENTATION   = 0x00000001
DM_PAPERSIZE     = 0x00000002
DM_PAPERLENGTH   = 0x00000004
DM_PAPERWIDTH    = 0x00000008
DM_SCALE         = 0x00000010
DM_COPIES        = 0x00000100
DM_DEFAULTSOURCE = 0x00000200
DM_PRINTQUALITY  = 0x00000400
DM_COLOR         = 0x00000800
DM_DUPLEX        = 0x00001000
DM_YRESOLUTION   = 0x00002000
DM_TTOPTION      = 0x00004000
DM_COLLATE       = 0x00008000
DM_FORMNAME      = 0x00010000

# Standard Windows dmPaperSize IDs
DMPAPER_LETTER    = 1
DMPAPER_TABLOID   = 3
DMPAPER_LEDGER    = 4
DMPAPER_LEGAL     = 5
DMPAPER_EXECUTIVE = 7
DMPAPER_A3        = 8
DMPAPER_A4        = 9
DMPAPER_A5        = 11
DMPAPER_B4        = 12
DMPAPER_B5        = 13
DMPAPER_A2        = 66
DMPAPER_A6        = 70
DMPAPER_USER      = 256

# Orientation
DMORIENT_PORTRAIT  = 1
DMORIENT_LANDSCAPE = 2

# Duplex
DMDUP_SIMPLEX    = 1
DMDUP_VERTICAL   = 2  # Long edge
DMDUP_HORIZONTAL = 3  # Short edge

# GDI DeviceCaps
HORZRES         = 8
VERTRES         = 10
LOGPIXELSX      = 88
LOGPIXELSY      = 90
PHYSICALWIDTH   = 110
PHYSICALHEIGHT  = 111
PHYSICALOFFSETX = 112
PHYSICALOFFSETY = 113

# Raster blit
BI_RGB         = 0
DIB_RGB_COLORS = 0
SRCCOPY        = 0x00CC0020
HALFTONE       = 4


# ── ctypes Struct Definitions ───────────────────────────────

if sys.platform == "win32" and wintypes is not None:
    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32),
            ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD),
            ("dmFields", wintypes.DWORD),
            ("dmOrientation", wintypes.SHORT),
            ("dmPaperSize", wintypes.SHORT),
            ("dmPaperLength", wintypes.SHORT),
            ("dmPaperWidth", wintypes.SHORT),
            ("dmScale", wintypes.SHORT),
            ("dmCopies", wintypes.SHORT),
            ("dmDefaultSource", wintypes.SHORT),
            ("dmPrintQuality", wintypes.SHORT),
            ("dmColor", wintypes.SHORT),
            ("dmDuplex", wintypes.SHORT),
            ("dmYResolution", wintypes.SHORT),
            ("dmTTOption", wintypes.SHORT),
            ("dmCollate", wintypes.SHORT),
            ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD),
            ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD),
            ("dmPelsHeight", wintypes.DWORD),
            ("dmNup", wintypes.DWORD),
            ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD),
            ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD),
            ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD),
            ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD),
            ("dmPanningHeight", wintypes.DWORD),
        ]

    class DOCINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.INT),
            ("lpszDocName", wintypes.LPCWSTR),
            ("lpszOutput", wintypes.LPCWSTR),
            ("lpszDatatype", wintypes.LPCWSTR),
            ("fwType", wintypes.DWORD),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]
else:
    DEVMODEW = None
    DOCINFOW = None
    BITMAPINFOHEADER = None


# ── Paper ID Mapping ────────────────────────────────────────

PAGE_SIZE_ID_MAP = {
    QPageSize.PageSizeId.A3: DMPAPER_A3,
    QPageSize.PageSizeId.A4: DMPAPER_A4,
    QPageSize.PageSizeId.A5: DMPAPER_A5,
    QPageSize.PageSizeId.A6: DMPAPER_A6,
    QPageSize.PageSizeId.A2: DMPAPER_A2,
    QPageSize.PageSizeId.Letter: DMPAPER_LETTER,
    QPageSize.PageSizeId.Legal: DMPAPER_LEGAL,
    QPageSize.PageSizeId.Tabloid: DMPAPER_TABLOID,
    QPageSize.PageSizeId.Ledger: DMPAPER_LEDGER,
    QPageSize.PageSizeId.Executive: DMPAPER_EXECUTIVE,
    QPageSize.PageSizeId.B4: DMPAPER_B4,
    QPageSize.PageSizeId.B5: DMPAPER_B5,
}


def map_qpagesize_to_dmpaper(page_size: Optional[QPageSize]) -> Tuple[int, Optional[float], Optional[float]]:
    """
    Map a QPageSize to a Windows dmPaperSize ID.
    Returns (dmPaperSize, width_mm, height_mm).
    """
    if page_size is None:
        return (DMPAPER_A4, None, None)

    # 1. Exact enum match
    ps_id = page_size.id()
    if ps_id in PAGE_SIZE_ID_MAP:
        return (PAGE_SIZE_ID_MAP[ps_id], None, None)

    # 2. Name check
    name_upper = page_size.name().strip().upper()
    if name_upper == "A3":
        return (DMPAPER_A3, None, None)
    if name_upper == "A4":
        return (DMPAPER_A4, None, None)
    if name_upper == "A5":
        return (DMPAPER_A5, None, None)
    if "TABLOID" in name_upper or "11X17" in name_upper:
        return (DMPAPER_TABLOID, None, None)

    # 3. Custom / SRA3
    sz_mm = page_size.size(QPageSize.Unit.Millimeter)
    return (DMPAPER_USER, float(sz_mm.width()), float(sz_mm.height()))


def is_windows() -> bool:
    """Check if running on Windows with winspool available."""
    return sys.platform == "win32" and winspool is not None and gdi32 is not None


# ── DEVMODE Management ──────────────────────────────────────

def get_native_devmode(printer_name: str) -> Optional[bytes]:
    """
    Retrieve default DEVMODE bytes for a given printer.
    Prioritizes per-user HKCU\\Printers\\DevModes2 cache to preserve private vendor plugin tokens.
    """
    if not is_windows():
        return None

    with com_initialized():
        # 1. First check HKCU\Printers\DevModes2 for user-configured driver state
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Printers\DevModes2")
            val, _ = winreg.QueryValueEx(key, printer_name)
            if val and len(val) >= ctypes.sizeof(DEVMODEW):
                return bytes(val)
        except Exception:
            pass

        # 2. Fallback to Winspool DocumentPropertiesW
        h_printer = wintypes.HANDLE()
        if not winspool.OpenPrinterW(printer_name, ctypes.byref(h_printer), None):
            return None

        try:
            needed = winspool.DocumentPropertiesW(None, h_printer, printer_name, None, None, 0)
            if needed <= 0:
                return None

            buf = ctypes.create_string_buffer(needed)
            res = winspool.DocumentPropertiesW(None, h_printer, printer_name, buf, None, DM_COPY)
            if res == IDOK:
                return buf.raw
            return None
        finally:
            winspool.ClosePrinter(h_printer)


def merge_devmode(
    printer_name: str,
    in_devmode: Optional[bytes] = None,
    paper_size_id: Optional[int] = None,
    orientation: Optional[int] = None,
    duplex: Optional[int] = None,
    copies: Optional[int] = None,
    collate: Optional[bool] = None,
    custom_width_mm: Optional[float] = None,
    custom_height_mm: Optional[float] = None,
) -> Optional[bytes]:
    """
    Merge user-selected prepress parameters into the printer driver's DEVMODE structure.
    Directly synchronizes both standard Win32 DEVMODE fields and proprietary vendor tokens
    (HP, Samsung, Xerox, Lexmark) in dmDriverExtra to guarantee the driver respects A3.
    """
    if not is_windows():
        return None

    with com_initialized():
        base_bytes = in_devmode or get_native_devmode(printer_name)
        if not base_bytes or len(base_bytes) < ctypes.sizeof(DEVMODEW):
            return None

        raw = bytearray(base_bytes)

        # 1. Update public DEVMODEW fields
        dev = DEVMODEW.from_buffer(raw)

        if orientation is not None:
            dev.dmFields |= DM_ORIENTATION
            dev.dmOrientation = int(orientation)

        if paper_size_id is not None:
            dev.dmFields |= (DM_PAPERSIZE | DM_FORMNAME)
            dev.dmPaperSize = int(paper_size_id)
            if paper_size_id == DMPAPER_A3:
                dev.dmFormName = "A3"
                dev.dmPaperLength = 4200
                dev.dmPaperWidth = 2970
                dev.dmFields |= (DM_PAPERLENGTH | DM_PAPERWIDTH)
            elif paper_size_id == DMPAPER_A4:
                dev.dmFormName = "A4"
                dev.dmPaperLength = 2970
                dev.dmPaperWidth = 2100
                dev.dmFields |= (DM_PAPERLENGTH | DM_PAPERWIDTH)
            elif paper_size_id == DMPAPER_USER and custom_width_mm and custom_height_mm:
                dev.dmFields |= (DM_PAPERLENGTH | DM_PAPERWIDTH)
                dev.dmPaperLength = int(round(custom_height_mm * 10))
                dev.dmPaperWidth = int(round(custom_width_mm * 10))

        if duplex is not None:
            dev.dmFields |= DM_DUPLEX
            dev.dmDuplex = int(duplex)

        if copies is not None and copies > 0:
            dev.dmFields |= DM_COPIES
            dev.dmCopies = int(copies)

        if collate is not None:
            dev.dmFields |= DM_COLLATE
            dev.dmCollate = 1 if collate else 0

        # 2. Patch proprietary vendor private tokens in dmDriverExtra (HP UPD, Lexmark, etc.)
        # This ensures vendor plugins synchronize with the public DEVMODE settings.
        if paper_size_id == DMPAPER_A3:
            raw = raw.replace(b"PageSize\x00A4\x00", b"PageSize\x00A3\x00")
        elif paper_size_id == DMPAPER_A4:
            raw = raw.replace(b"PageSize\x00A3\x00", b"PageSize\x00A4\x00")

        if orientation == DMORIENT_LANDSCAPE:
            raw = raw.replace(b"HPOrientation\x00HPOrientationPortrait\x00", b"HPOrientation\x00HPOrientationLandscape\x00")
        elif orientation == DMORIENT_PORTRAIT:
            raw = raw.replace(b"HPOrientation\x00HPOrientationLandscape\x00", b"HPOrientation\x00HPOrientationPortrait\x00")

        # 3. Synchronize dmDriverExtra header length with the buffer
        dev = DEVMODEW.from_buffer(raw)
        dev.dmDriverExtra = len(raw) - dev.dmSize

        return bytes(raw)


def open_native_driver_dialog(
    hwnd: int,
    printer_name: str,
    in_devmode: Optional[bytes] = None,
) -> Optional[bytes]:
    """
    Open the printer manufacturer's native configuration dialog directly.
    Bypasses the generic Windows preview dialog.
    Returns the updated DEVMODE bytes if user clicked OK, or None if cancelled.
    """
    if not is_windows():
        return None

    with com_initialized():
        h_printer = wintypes.HANDLE()
        if not winspool.OpenPrinterW(printer_name, ctypes.byref(h_printer), None):
            return None

        try:
            needed = winspool.DocumentPropertiesW(None, h_printer, printer_name, None, None, 0)
            if needed <= 0:
                return None

            if in_devmode and len(in_devmode) >= needed:
                in_buf = ctypes.create_string_buffer(in_devmode, len(in_devmode))
            else:
                in_buf = ctypes.create_string_buffer(needed)
                winspool.DocumentPropertiesW(None, h_printer, printer_name, in_buf, None, DM_COPY)

            out_buf = ctypes.create_string_buffer(needed)
            res = winspool.DocumentPropertiesW(
                hwnd, h_printer, printer_name, out_buf, in_buf, DM_PROMPT_AND_BUFFER,
            )
            if res == IDOK:
                return out_buf.raw
            return None
        finally:
            winspool.ClosePrinter(h_printer)


def parse_devmode_settings(devmode_bytes: bytes) -> dict:
    """Extract standard prepress fields from a DEVMODE bytes buffer."""
    if not is_windows() or not devmode_bytes or len(devmode_bytes) < ctypes.sizeof(DEVMODEW):
        return {}

    dev = DEVMODEW.from_buffer_copy(devmode_bytes)
    return {
        "device_name": str(dev.dmDeviceName),
        "paper_size": int(dev.dmPaperSize),
        "orientation": int(dev.dmOrientation),
        "duplex": int(dev.dmDuplex),
        "copies": int(dev.dmCopies),
        "collate": bool(dev.dmCollate),
        "form_name": str(dev.dmFormName),
        "paper_width_tenth_mm": int(dev.dmPaperWidth),
        "paper_length_tenth_mm": int(dev.dmPaperLength),
    }


def get_printer_hardware_margins(
    printer_name: str,
    devmode_bytes: Optional[bytes] = None,
) -> Tuple[float, float, float, float]:
    """
    Query the true physical unprintable hardware margins (left, top, right, bottom)
    in points (pt) from the printer driver.

    Uses Windows GDI GetDeviceCaps (PHYSICALOFFSETX, PHYSICALOFFSETY, PHYSICALWIDTH,
    PHYSICALHEIGHT, HORZRES, VERTRES, LOGPIXELSX, LOGPIXELSY) on an Information Context
    (CreateICW), ensuring zero print jobs are queued while obtaining exact roller margins.
    """
    fallback_pt = 4.2 * 72.0 / 25.4  # ~11.9 pt (~4.2 mm standard roller inset)
    if not is_windows() or not printer_name or gdi32 is None:
        return (fallback_pt, fallback_pt, fallback_pt, fallback_pt)

    hdc = None
    try:
        devmode_buf = None
        if devmode_bytes:
            devmode_buf = ctypes.create_string_buffer(devmode_bytes, len(devmode_bytes))
        # CreateICW creates an Information Context: fast, read-only device metrics
        hdc = gdi32.CreateICW("WINSPOOL", printer_name, None, devmode_buf)
        if not hdc:
            hdc = gdi32.CreateDCW("WINSPOOL", printer_name, None, devmode_buf)
        if not hdc:
            return (fallback_pt, fallback_pt, fallback_pt, fallback_pt)

        dpi_x = gdi32.GetDeviceCaps(hdc, LOGPIXELSX) or 600
        dpi_y = gdi32.GetDeviceCaps(hdc, LOGPIXELSY) or 600
        phys_w = gdi32.GetDeviceCaps(hdc, PHYSICALWIDTH)
        phys_h = gdi32.GetDeviceCaps(hdc, PHYSICALHEIGHT)
        off_x = gdi32.GetDeviceCaps(hdc, PHYSICALOFFSETX)
        off_y = gdi32.GetDeviceCaps(hdc, PHYSICALOFFSETY)
        printable_w = gdi32.GetDeviceCaps(hdc, HORZRES)
        printable_h = gdi32.GetDeviceCaps(hdc, VERTRES)

        m_left_pt = max(0.0, off_x * 72.0 / dpi_x)
        m_top_pt = max(0.0, off_y * 72.0 / dpi_y)
        m_right_pt = max(0.0, (phys_w - off_x - printable_w) * 72.0 / dpi_x)
        m_bottom_pt = max(0.0, (phys_h - off_y - printable_h) * 72.0 / dpi_y)

        # If margins are completely 0 (e.g. virtual PDF or edge-to-edge), provide
        # standard 4.2mm printable margin guide so user sees the safety zone.
        if m_left_pt < 0.5 and m_top_pt < 0.5 and m_right_pt < 0.5 and m_bottom_pt < 0.5:
            return (fallback_pt, fallback_pt, fallback_pt, fallback_pt)

        return (m_left_pt, m_top_pt, m_right_pt, m_bottom_pt)
    except Exception:
        return (fallback_pt, fallback_pt, fallback_pt, fallback_pt)
    finally:
        if hdc and gdi32:
            gdi32.DeleteDC(hdc)


# ── Direct GDI Spooler Printing ─────────────────────────────

def print_pdf_direct_gdi(
    printer_name: str,
    devmode_bytes: bytes,
    doc_path: str,
    sheet_indices: List[int],
    scaling_mode: str = "actual",
    center: bool = True,
    resolution: int = 600,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    doc_name: str = "Laydown Stampa",
    output_file: Optional[str] = None,
) -> None:
    """
    Spool an imposed PDF directly to the Windows printer DC via GDI.

    Uses CreateDCW with the pre-configured DEVMODE (guaranteeing exact paper size
    like A3 and orientation without Windows driver auto-downscaling to A4).
    Renders pages via PyMuPDF at requested resolution (150 DPI for draft, 600+ DPI
    for production) and transfers rasters using StretchDIBits.
    """
    if not is_windows():
        raise RuntimeError("Direct GDI spooling is only supported on Windows.")

    with com_initialized():
        devmode_buf = ctypes.create_string_buffer(devmode_bytes, len(devmode_bytes))
        hdc = gdi32.CreateDCW("WINSPOOL", printer_name, None, devmode_buf)
        if not hdc:
            raise RuntimeError(f"Impossibile creare il Device Context GDI per la stampante '{printer_name}'.")

        doc = None
        try:
            # Query device metrics
            dpi_x = gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
            dpi_y = gdi32.GetDeviceCaps(hdc, LOGPIXELSY)
            printable_w = gdi32.GetDeviceCaps(hdc, HORZRES)
            printable_h = gdi32.GetDeviceCaps(hdc, VERTRES)
            phys_w = gdi32.GetDeviceCaps(hdc, PHYSICALWIDTH)
            phys_h = gdi32.GetDeviceCaps(hdc, PHYSICALHEIGHT)
            off_x = gdi32.GetDeviceCaps(hdc, PHYSICALOFFSETX)
            off_y = gdi32.GetDeviceCaps(hdc, PHYSICALOFFSETY)

            # Set high-quality halftone blit mode
            gdi32.SetStretchBltMode(hdc, HALFTONE)
            if user32:
                gdi32.SetBrushOrgEx(hdc, 0, 0, None)

            docinfo = DOCINFOW()
            docinfo.cbSize = ctypes.sizeof(DOCINFOW)
            docinfo.lpszDocName = doc_name
            docinfo.lpszOutput = output_file
            docinfo.lpszDatatype = None
            docinfo.fwType = 0

            job_id = gdi32.StartDocW(hdc, ctypes.byref(docinfo))
            if job_id <= 0:
                raise RuntimeError(f"StartDocW fallito sul driver di stampa (codice {job_id}).")

            doc = fitz.open(doc_path)
            total = len(sheet_indices)

            paper_w_pt = phys_w * 72.0 / dpi_x
            paper_h_pt = phys_h * 72.0 / dpi_y

            for i, sheet_idx in enumerate(sheet_indices):
                if cancel_check and cancel_check():
                    gdi32.AbortDoc(hdc)
                    return

                if progress_cb:
                    progress_cb(i + 1, total)

                if sheet_idx < 0 or sheet_idx >= len(doc):
                    continue

                page = doc[sheet_idx]
                sheet_w_pt = float(page.rect.width)
                sheet_h_pt = float(page.rect.height)

                # Placement calculation
                if scaling_mode == "fit":
                    scale = min(paper_w_pt / sheet_w_pt, paper_h_pt / sheet_h_pt)
                elif scaling_mode == "shrink":
                    if sheet_w_pt > paper_w_pt or sheet_h_pt > paper_h_pt:
                        scale = min(paper_w_pt / sheet_w_pt, paper_h_pt / sheet_h_pt)
                    else:
                        scale = 1.0
                else:  # "actual" (100% 1:1)
                    scale = 1.0

                target_w_pt = sheet_w_pt * scale
                target_h_pt = sheet_h_pt * scale

                if center:
                    sheet_pos_x_pt = (paper_w_pt - target_w_pt) / 2.0
                    sheet_pos_y_pt = (paper_h_pt - target_h_pt) / 2.0
                else:
                    sheet_pos_x_pt = 0.0
                    sheet_pos_y_pt = 0.0

                # Destination coordinates on the HDC (accounting for hardware margins off_x, off_y)
                dest_x = int(round((sheet_pos_x_pt * dpi_x / 72.0) - off_x))
                dest_y = int(round((sheet_pos_y_pt * dpi_y / 72.0) - off_y))
                dest_w = int(round(target_w_pt * dpi_x / 72.0))
                dest_h = int(round(target_h_pt * dpi_y / 72.0))

                # Render sheet via PyMuPDF at chosen resolution (e.g. 150 for draft, 600 for HD)
                render_scale = resolution / 72.0
                matrix = fitz.Matrix(
                    render_scale * (target_w_pt / sheet_w_pt),
                    render_scale * (target_h_pt / sheet_h_pt),
                )
                pix = page.get_pixmap(matrix=matrix, alpha=False)

                # Convert to BGR888 for Windows DIB
                qimg = QImage(
                    pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888,
                ).convertToFormat(QImage.Format.Format_BGR888)

                raw_bytes = qimg.constBits().asstring(qimg.sizeInBytes())

                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = qimg.width()
                bmi.biHeight = -qimg.height()  # Top-down DIB
                bmi.biPlanes = 1
                bmi.biBitCount = 24
                bmi.biCompression = BI_RGB
                bmi.biSizeImage = qimg.sizeInBytes()

                gdi32.StartPage(hdc)
                gdi32.StretchDIBits(
                    hdc,
                    dest_x, dest_y, dest_w, dest_h,
                    0, 0, qimg.width(), qimg.height(),
                    raw_bytes,
                    ctypes.byref(bmi),
                    DIB_RGB_COLORS,
                    SRCCOPY,
                )
                gdi32.EndPage(hdc)

                del raw_bytes
                del qimg
                del pix

            gdi32.EndDoc(hdc)
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
            gdi32.DeleteDC(hdc)


# ── Direct Vector PostScript Spooler Printing ───────────────

def print_pdf_direct_vector_ps(
    printer_name: str,
    devmode_bytes: Optional[bytes],
    doc_path: str,
    sheet_indices: List[int],
    scaling_mode: str = "actual",
    center: bool = True,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    doc_name: str = "Laydown Stampa Vettoriale",
    output_file: Optional[str] = None,
    page_size: Optional[QPageSize] = None,
    orientation: Optional[QPageLayout.Orientation] = None,
    copies: int = 1,
    collate: bool = True,
    duplex: Optional[QPrinter.DuplexMode] = None,
) -> None:
    """
    Spool an imposed PDF to a Windows PostScript / production printer using pure vector primitives.

    Preserves 100% vector resolution: text glyphs, Bezier curves, and crop/cut marks are emitted
    as native vector draw calls via PyMuPDF's SVG engine and QSvgRenderer directly onto QPrinter's
    underlying PostScript/GDI device context. No rasterization or pixelation occurs prior to spooling,
    allowing the printer's hardware RIP (e.g. Ricoh, Canon, HP UPD PS, Fiery) to rasterize at native
    optical hardware resolution (1200x1200 / 2400 DPI) with its proprietary screening algorithms.
    """
    with com_initialized():
        p_info = None
        if printer_name:
            for info in QPrinterInfo.availablePrinters():
                if info.printerName() == printer_name:
                    p_info = info
                    break

        if p_info is not None:
            printer = QPrinter(p_info, QPrinter.PrinterMode.HighResolution)
        else:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            if printer_name:
                printer.setPrinterName(printer_name)

        printer.setDocName(doc_name)
        printer.setFullPage(True)
        if output_file:
            printer.setOutputFileName(output_file)

        if page_size is not None:
            printer.setPageSize(page_size)
        if orientation is not None:
            printer.setPageOrientation(orientation)

        if devmode_bytes and len(devmode_bytes) >= 46:
            import struct
            paper_size_id = struct.unpack_from("<h", devmode_bytes, 44)[0]
            pe = printer.printEngine()
            if pe is not None:
                if paper_size_id > 0:
                    pe.setProperty(QPrintEngine.PrintEnginePropertyKey.PPK_WindowsPageSize, paper_size_id)
                pe.setProperty(QPrintEngine.PrintEnginePropertyKey.PPK_CopyCount, max(1, copies))
                pe.setProperty(QPrintEngine.PrintEnginePropertyKey.PPK_CollateCopies, collate)

        printer.setCopyCount(max(1, copies))
        printer.setCollateCopies(collate)
        if duplex is not None:
            printer.setDuplex(duplex)

        painter = QPainter(printer)
        if not painter.isActive():
            raise RuntimeError(f"Impossibile inizializzare QPainter per la stampante '{printer_name}'.")

        doc = None
        try:
            full_rect = printer.pageLayout().fullRectPoints()
            paper_w_pt = float(full_rect.width())
            paper_h_pt = float(full_rect.height())
            if paper_w_pt <= 0 or paper_h_pt <= 0:
                res = printer.resolution()
                paper_w_pt = float(printer.width()) * 72.0 / res
                paper_h_pt = float(printer.height()) * 72.0 / res

            scale_dev = printer.resolution() / 72.0

            doc = fitz.open(doc_path)
            total = len(sheet_indices)

            for i, sheet_idx in enumerate(sheet_indices):
                if cancel_check and cancel_check():
                    break

                if progress_cb:
                    progress_cb(i + 1, total)

                if i > 0:
                    printer.newPage()

                if sheet_idx < 0 or sheet_idx >= len(doc):
                    continue

                page = doc[sheet_idx]
                sheet_w_pt = float(page.rect.width)
                sheet_h_pt = float(page.rect.height)

                # Placement calculation
                if scaling_mode == "fit":
                    scale = min(paper_w_pt / sheet_w_pt, paper_h_pt / sheet_h_pt)
                elif scaling_mode == "shrink":
                    if sheet_w_pt > paper_w_pt or sheet_h_pt > paper_h_pt:
                        scale = min(paper_w_pt / sheet_w_pt, paper_h_pt / sheet_h_pt)
                    else:
                        scale = 1.0
                else:  # "actual" (100% 1:1)
                    scale = 1.0

                target_w_pt = sheet_w_pt * scale
                target_h_pt = sheet_h_pt * scale

                if center:
                    offset_x_pt = (paper_w_pt - target_w_pt) / 2.0
                    offset_y_pt = (paper_h_pt - target_h_pt) / 2.0
                else:
                    offset_x_pt = 0.0
                    offset_y_pt = 0.0

                target_rect = QRectF(
                    offset_x_pt * scale_dev,
                    offset_y_pt * scale_dev,
                    target_w_pt * scale_dev,
                    target_h_pt * scale_dev,
                )

                svg_text = page.get_svg_image()
                svg_bytes = QByteArray(svg_text.encode("utf-8"))
                renderer = QSvgRenderer(svg_bytes)
                if renderer.isValid():
                    renderer.render(painter, target_rect)
        finally:
            painter.end()
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
