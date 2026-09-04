"""Data export handler for CSV and Excel formats."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

from spse_crawler.config.settings import Settings, get_settings
from spse_crawler.models.tender import TenderDetail


class DataExporter:
    """Exports TenderDetail records to CSV and Excel files.

    Output is written to ``{export_dir}/{kode_instansi}/`` and also
    to a combined root export directory.
    """

    EXPORT_COLUMNS: list[str] = [
        "kode_instansi",
        "id_lelang",
        "nama_paket",
        "instansi",
        "hps",
        "jenis_pengadaan",
        "tahap_saat_ini",
        "is_prakualifikasi",
        "url_pengumuman",
        "scraped_at",
    ]

    EXPORT_HEADERS: dict[str, str] = {
        "kode_instansi": "Kode Instansi",
        "id_lelang": "ID Lelang",
        "nama_paket": "Nama Paket",
        "instansi": "Instansi",
        "hps": "HPS (Rp)",
        "jenis_pengadaan": "Jenis Pengadaan",
        "tahap_saat_ini": "Tahap Saat Ini",
        "is_prakualifikasi": "Prakualifikasi",
        "url_pengumuman": "URL Pengumuman",
        "scraped_at": "Waktu Scrape",
    }

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._export_dir = Path(self._settings.export_dir)
        self._export_dir.mkdir(parents=True, exist_ok=True)

    def _build_dataframe(self, details: list[TenderDetail]) -> pd.DataFrame:
        """Convert a list of TenderDetail to a formatted DataFrame."""
        records: list[dict[str, object]] = []
        for d in details:
            records.append({
                "kode_instansi": d.kode_instansi,
                "id_lelang": d.id_lelang,
                "nama_paket": d.nama_paket,
                "instansi": d.instansi,
                "hps": d.hps,
                "jenis_pengadaan": d.jenis_pengadaan,
                "tahap_saat_ini": d.tahap_saat_ini,
                "is_prakualifikasi": d.is_prakualifikasi,
                "url_pengumuman": d.url_pengumuman,
                "scraped_at": d.scraped_at.strftime("%Y-%m-%d %H:%M:%S"),
            })
        df = pd.DataFrame(records, columns=self.EXPORT_COLUMNS)
        df.rename(columns=self.EXPORT_HEADERS, inplace=True)
        return df

    def export_csv(
        self,
        details: list[TenderDetail],
        filename: str | None = None,
    ) -> Path:
        """Export details to a CSV file.

        Args:
            details: List of TenderDetail records.
            filename: Custom filename (without extension). Auto-generated if None.

        Returns:
            Path to the written CSV file.
        """
        if not details:
            logger.warning("No details to export")
            return self._export_dir / "empty.csv"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if filename is None:
            instansi_code = details[0].kode_instansi
            filename = f"prakualifikasi_{instansi_code}_{timestamp}"

        filepath = self._export_dir / f"{filename}.csv"
        df = self._build_dataframe(details)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")

        logger.info(
            "CSV exported: {} ({} records, {} prakualifikasi)",
            filepath,
            len(df),
            int(df[self.EXPORT_HEADERS["is_prakualifikasi"]].sum()),
        )
        return filepath

    def export_excel(
        self,
        details: list[TenderDetail],
        filename: str | None = None,
    ) -> Path:
        """Export details to an Excel (.xlsx) file.

        Args:
            details: List of TenderDetail records.
            filename: Custom filename (without extension). Auto-generated if None.

        Returns:
            Path to the written Excel file.
        """
        if not details:
            logger.warning("No details to export")
            return self._export_dir / "empty.xlsx"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if filename is None:
            instansi_code = details[0].kode_instansi
            filename = f"prakualifikasi_{instansi_code}_{timestamp}"

        filepath = self._export_dir / f"{filename}.xlsx"
        df = self._build_dataframe(details)

        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Prakualifikasi")

            # Auto-adjust column widths
            ws = writer.sheets["Prakualifikasi"]
            for i, col in enumerate(df.columns, 1):
                max_len = max(
                    df[col].astype(str).map(len).max(),
                    len(col),
                ) + 2
                ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = min(max_len, 50)

        logger.info(
            "Excel exported: {} ({} records)",
            filepath, len(df),
        )
        return filepath

    def export_combined(
        self,
        all_details: list[TenderDetail],
        filename: str | None = None,
    ) -> tuple[Path, Path]:
        """Export all details (from all instansi) to both CSV and Excel.

        Returns:
            Tuple of (csv_path, xlsx_path).
        """
        if not all_details:
            logger.warning("No combined details to export")
            return self._export_dir / "empty.csv", self._export_dir / "empty.xlsx"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if filename is None:
            filename = f"all_prakualifikasi_{timestamp}"

        csv_path = self._export_dir / f"{filename}.csv"
        xlsx_path = self._export_dir / f"{filename}.xlsx"
        df = self._build_dataframe(all_details)

        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Prakualifikasi")

        logger.info(
            "Combined export: {} + {} ({} total records)",
            csv_path.name, xlsx_path.name, len(df),
        )
        return csv_path, xlsx_path
