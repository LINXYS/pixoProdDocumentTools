from __future__ import annotations

import ftplib
import logging
import socket
import os
import stat
from pathlib import Path
from typing import Dict, Any


def sync_remote_to_local(remote_cfg: Dict[str, Any], local_root: Path) -> None:
    """
    Sync files from a remote FTP/FTPS/SFTP server into `local_root`.

    remote_cfg keys (all optional except protocol + host):
      - protocol: "ftp", "ftps", or "sftp"
      - host: hostname or IP
      - port: integer (default 21 for ftp/ftps, 22 for sftp)
      - username: username (may be None/anonymous depending on server)
      - password: password (may be None for key-based auth, not yet supported)
      - remote_path: path on the remote server (default ".")
      - passive: bool (FTP/FTPS only)
      - recursive: bool (default True)

    Files are downloaded into `local_root`, overwriting any existing files
    with the same relative path.
    """
    if not remote_cfg:
        return

    protocol = (remote_cfg.get("protocol") or "").lower()
    host = remote_cfg.get("host")
    if not protocol or not host:
        logging.info("Remote source configuration is incomplete; skipping remote sync.")
        return

    port = remote_cfg.get("port")
    if not port:
        port = 21 if protocol in ("ftp", "ftps") else 22

    username = remote_cfg.get("username") or ""
    password = remote_cfg.get("password") or ""
    remote_path = remote_cfg.get("remote_path") or "."
    recursive = bool(remote_cfg.get("recursive", True))

    local_root = Path(local_root)
    local_root.mkdir(parents=True, exist_ok=True)

    logging.info(
        f"Starting remote sync: protocol={protocol}, host={host}, port={port}, "
        f"remote_path={remote_path}, recursive={recursive}"
    )

    try:
        if protocol in ("ftp", "ftps"):
            passive = bool(remote_cfg.get("passive", True))
            _sync_ftp_ftps(
                protocol=protocol,
                host=host,
                port=port,
                username=username,
                password=password,
                remote_path=remote_path,
                local_root=local_root,
                passive=passive,
                recursive=recursive,
            )
        elif protocol == "sftp":
            _sync_sftp(
                host=host,
                port=port,
                username=username,
                password=password,
                remote_path=remote_path,
                local_root=local_root,
                recursive=recursive,
            )
        else:
            raise RuntimeError(f"Unsupported remote protocol: {protocol}")
    except Exception as e:
        # Don't leak credentials in logs
        raise RuntimeError(f"Remote sync failed for {protocol}://{host}:{port}{remote_path!r}: {e}") from e


def _sync_ftp_ftps(
    protocol: str,
    host: str,
    port: int,
    username: str,
    password: str,
    remote_path: str,
    local_root: Path,
    passive: bool = True,
    recursive: bool = True,
    connect_timeout: float = 30.0,
    op_timeout: float = 30.0,
) -> None:
    """
    FTP/FTPS sync with basic diagnostics and timeouts to avoid infinite hangs.
    """
    cls = ftplib.FTP_TLS if protocol == "ftps" else ftplib.FTP

    logging.info(
        f"[FTP] Connecting to {host}:{port} "
        f"(protocol={protocol}, passive={passive}, remote_path={remote_path})"
    )

    # Set a global default timeout for sockets used by ftplib operations.
    # This prevents nlst/retrbinary from hanging indefinitely.
    prev_default_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(op_timeout)

    try:
        with cls() as ftp:
            try:
                ftp.connect(host, port, timeout=connect_timeout)
                logging.info("[FTP] Connected, logging in...")
                ftp.login(user=username, passwd=password)
                logging.info(f"[FTP] Logged in as '{username or 'anonymous'}'")
            except ftplib.all_errors as e:
                raise RuntimeError(f"FTP/FTPS connect/login failed: {e}")

            if protocol == "ftps":
                # Secure the control and data connection
                ftp.prot_p()
                try:
                    ftp.set_pasv(passive)
                    logging.info(f"[FTP] Passive mode set to {passive}")
                except ftplib.all_errors as e:
                    raise RuntimeError(f"Failed to set passive mode to {passive}: {e}")
            else:
                try:
                    ftp.set_pasv(passive)
                    logging.info(f"[FTP] Passive mode set to {passive}")
                except ftplib.all_errors as e:
                    raise RuntimeError(f"Failed to set passive mode to {passive}: {e}")

            def walk_and_download(path: str, dest_root: Path) -> None:
                logging.info(f"[FTP] Entering directory: {path}")
                try:
                    ftp.cwd(path)
                except ftplib.all_errors as e:
                    raise RuntimeError(f"Cannot change directory to {path!r}: {e}")

                try:
                    logging.info(f"[FTP] Listing directory: {path}")
                    items = ftp.nlst()
                except ftplib.all_errors as e:
                    raise RuntimeError(f"Directory listing failed for {path!r}: {e}")

                for name in items:
                    if name in (".", ".."):
                        continue
                    is_dir = False
                    try:
                        ftp.cwd(name)
                        ftp.cwd("..")
                        is_dir = True
                    except ftplib.all_errors:
                        is_dir = False

                    remote_item = f"{path.rstrip('/')}/{name}"
                    local_item = dest_root / name

                    if is_dir:
                        if recursive:
                            local_item.mkdir(parents=True, exist_ok=True)
                            walk_and_download(remote_item, local_item)
                        else:
                            logging.info(f"[FTP] Skipping subdirectory (recursive disabled): {remote_item}")
                    else:
                        local_item.parent.mkdir(parents=True, exist_ok=True)
                        logging.info(f"[FTP] Downloading: {remote_item} -> {local_item}")
                        try:
                            with open(local_item, "wb") as f:
                                ftp.retrbinary(f"RETR {remote_item}", f.write)
                        except ftplib.all_errors as e:
                            raise RuntimeError(f"Failed to download {remote_item!r}: {e}")

            walk_and_download(remote_path, local_root)
            logging.info("[FTP] Sync completed successfully.")
    finally:
        # Restore previous global default timeout
        socket.setdefaulttimeout(prev_default_timeout)


def _sync_sftp(
    host: str,
    port: int,
    username: str,
    password: str,
    remote_path: str,
    local_root: Path,
    recursive: bool = True,
) -> None:
    try:
        import paramiko
    except ImportError as e:
        raise RuntimeError("paramiko is required for SFTP support but is not installed.") from e

    transport = paramiko.Transport((host, port))
    try:
        transport.connect(username=username or None, password=password or None)
        sftp = paramiko.SFTPClient.from_transport(transport)

        def walk_and_download(path: str, dest_root: Path) -> None:
            for attr in sftp.listdir_attr(path):
                name = attr.filename
                if name in (".", ".."):
                    continue
                remote_item = f"{path.rstrip('/')}/{name}"
                local_item = dest_root / name
                if stat.S_ISDIR(attr.st_mode):
                    if recursive:
                        local_item.mkdir(parents=True, exist_ok=True)
                        walk_and_download(remote_item, local_item)
                else:
                    local_item.parent.mkdir(parents=True, exist_ok=True)
                    logging.info(f"Downloading SFTP file: {remote_item} -> {local_item}")
                    sftp.get(remote_item, str(local_item))

        walk_and_download(remote_path, local_root)
    finally:
        transport.close()
