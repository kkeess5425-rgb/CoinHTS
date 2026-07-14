"""
core/settings_manager.py
========================
설정 백업/복원 + 로그 뷰어 + 오류 자동 보고.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SettingsManager:
    """설정 백업/복원 관리자."""

    def __init__(self, config_path: str = "config.json",
                 backup_dir: str = "backups/settings") -> None:
        self._config_path = Path(config_path)
        self._backup_dir  = Path(backup_dir)
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    def backup(self, label: str = "") -> str:
        """현재 설정을 백업 파일로 저장."""
        if not self._config_path.exists():
            logger.warning(f"[Settings] {self._config_path} 없음")
            return ""
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        name  = f"config_{ts}{'_' + label if label else ''}.json"
        dest  = self._backup_dir / name
        shutil.copy2(self._config_path, dest)
        logger.info(f"[Settings] 백업: {dest}")
        return str(dest)

    def restore(self, backup_path: str) -> bool:
        """백업 파일에서 설정 복원."""
        src = Path(backup_path)
        if not src.exists():
            logger.error(f"[Settings] 백업 파일 없음: {src}")
            return False
        # 현재 설정 자동 백업
        self.backup(label="before_restore")
        shutil.copy2(src, self._config_path)
        logger.info(f"[Settings] 복원 완료: {src} → {self._config_path}")
        return True

    def list_backups(self) -> list[dict]:
        """백업 목록 조회."""
        items = []
        for f in sorted(self._backup_dir.glob("config_*.json"), reverse=True):
            stat = f.stat()
            items.append({
                "name":    f.name,
                "path":    str(f),
                "size":    stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return items

    def export_json(self) -> dict:
        """현재 설정을 딕셔너리로 반환."""
        if not self._config_path.exists():
            return {}
        with open(self._config_path) as f:
            return json.load(f)

    def import_json(self, data: dict) -> bool:
        """딕셔너리를 설정 파일로 저장."""
        try:
            self.backup(label="before_import")
            with open(self._config_path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("[Settings] 설정 가져오기 완료")
            return True
        except Exception as e:
            logger.error(f"[Settings] 가져오기 실패: {e}")
            return False


class LogViewer:
    """실시간 로그 뷰어."""

    def __init__(self, log_path: str = "logs/coinhts.log",
                 max_lines: int = 1000) -> None:
        self._log_path = Path(log_path)
        self._max_lines = max_lines
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

        # 파일 핸들러 추가
        root = logging.getLogger()
        if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
            fh = logging.FileHandler(self._log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            root.addHandler(fh)

    def get_recent(self, n: int = 100, level: str = "ALL") -> list[dict]:
        """최근 N줄 로그 조회."""
        if not self._log_path.exists():
            return []
        try:
            with open(self._log_path, encoding="utf-8") as f:
                lines = f.readlines()[-n:]
        except Exception:
            return []

        result = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # level 필터
            if level != "ALL":
                if f"[{level}]" not in line:
                    continue
            result.append({"text": line})
        return result

    def clear(self) -> None:
        """로그 파일 초기화."""
        if self._log_path.exists():
            open(self._log_path, "w").close()


class ErrorReporter:
    """오류 자동 수집 및 보고."""

    def __init__(self) -> None:
        self._errors: list[dict] = []
        self._max = 500

    def capture(self, error: Exception, context: str = "") -> None:
        """오류 캡처."""
        import traceback
        entry = {
            "ts":      time.time(),
            "context": context,
            "type":    type(error).__name__,
            "message": str(error),
            "trace":   traceback.format_exc(),
        }
        self._errors.append(entry)
        if len(self._errors) > self._max:
            self._errors = self._errors[-self._max:]
        logger.error(f"[ErrorReporter] {context}: {type(error).__name__}: {error}")

    def get_recent(self, n: int = 20) -> list[dict]:
        return self._errors[-n:]

    def get_stats(self) -> dict:
        from collections import Counter
        type_counts = Counter(e["type"] for e in self._errors)
        return {
            "total":      len(self._errors),
            "by_type":    dict(type_counts.most_common(10)),
            "last_error": self._errors[-1] if self._errors else None,
        }

    def export(self, path: str = "logs/errors.json") -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._errors, f, indent=2, ensure_ascii=False)
