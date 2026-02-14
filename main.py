#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rich",
#     "requests",
# ]
# ///
import os
import sys
import json
import hashlib
import requests
import tarfile
import shutil
import platform
import concurrent.futures
import argparse
import tempfile
import time
import sqlite3
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timedelta
import fcntl
from functools import lru_cache
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn
from rich.panel import Panel
from rich import box
from rich.prompt import Confirm

VERSION = "0.0.12-alpha"
BASE_DIR = Path.home() / ".br"
CELLAR = BASE_DIR / "Cellar"
BIN_DIR = BASE_DIR / "bin"
CACHE_DIR = BASE_DIR / "cache"
INVENTORY_FILE = BASE_DIR / "inventory.json"
CACHE_DB = CACHE_DIR / "metadata.db"
MAX_PARALLEL_DOWNLOADS = 5
CACHE_TTL_HOURS = 6  
HEADERS = {"User-Agent": "BrPackageManager/0.2"}
REQUEST_TIMEOUT = 15
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2

OS_MAP: dict[str, str] = {
    "26": "tahoe", "15": "sequoia", "14": "sonoma", "13": "ventura",
    "12": "monterey", "11": "big_sur", "10.15": "catalina"
}

def get_os_flavor() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux": return "x86_64_linux"
    elif system == "darwin":
        mac_ver = platform.mac_ver()[0].split(".")[0]
        arch = "arm64" if machine == "arm64" else "x86_64"
        name = OS_MAP.get(mac_ver, "ventura")
        return f"{arch}_{name}"
    else:
        raise OSError("Unsupported Operating System")

OS_FLAVOR = get_os_flavor()


class MetadataCache:
    """SQLite-based cache for package metadata with TTL support."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize the cache database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata_cache (
                    package_name TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    cached_at REAL NOT NULL,
                    ttl_hours INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cached_at 
                ON metadata_cache(cached_at)
            """)
            conn.commit()
    
    def get(self, package_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached metadata if still valid."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT data, cached_at, ttl_hours 
                FROM metadata_cache 
                WHERE package_name = ?
            """, (package_name,))
            row = cursor.fetchone()
            
            if row:
                data, cached_at, ttl_hours = row
                age_hours = (time.time() - cached_at) / 3600
                
                if age_hours < ttl_hours:
                    return json.loads(data)
                else:
                    # Expired, delete it
                    conn.execute("DELETE FROM metadata_cache WHERE package_name = ?", (package_name,))
                    conn.commit()
        
        return None
    
    def set(self, package_name: str, data: Dict[str, Any], ttl_hours: int = CACHE_TTL_HOURS):
        """Store metadata in cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO metadata_cache (package_name, data, cached_at, ttl_hours)
                VALUES (?, ?, ?, ?)
            """, (package_name, json.dumps(data), time.time(), ttl_hours))
            conn.commit()
    
    def invalidate(self, package_name: str):
        """Remove a package from cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM metadata_cache WHERE package_name = ?", (package_name,))
            conn.commit()
    
    def clear_expired(self):
        """Remove all expired cache entries."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT package_name, cached_at, ttl_hours FROM metadata_cache")
            now = time.time()
            expired = []
            
            for pkg_name, cached_at, ttl_hours in cursor.fetchall():
                if (now - cached_at) / 3600 >= ttl_hours:
                    expired.append(pkg_name)
            
            if expired:
                conn.executemany("DELETE FROM metadata_cache WHERE package_name = ?", 
                                [(pkg,) for pkg in expired])
                conn.commit()
            
            return len(expired)
    
    def stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM metadata_cache")
            total = cursor.fetchone()[0]
            
            # Count expired
            cursor = conn.execute("SELECT cached_at, ttl_hours FROM metadata_cache")
            now = time.time()
            expired = sum(1 for cached_at, ttl in cursor.fetchall() 
                         if (now - cached_at) / 3600 >= ttl)
            
            return {"total": total, "valid": total - expired, "expired": expired}


class Brewery:
    def __init__(self, verbose: bool = False) -> None:
        self.console = Console()
        self.verbose = verbose
        for folder in [CELLAR, BIN_DIR, CACHE_DIR]:
            folder.mkdir(parents=True, exist_ok=True)
        
        # Initialize metadata cache
        self.metadata_cache = MetadataCache(CACHE_DB)
        
        if INVENTORY_FILE.exists():
            try:
                with open(INVENTORY_FILE, 'r') as f:
                    self.inventory = json.load(f)
            except json.JSONDecodeError:
                self.inventory = {}
        else:
            self.inventory = {}
        
        # Dependency resolution cache (in-memory for session)
        self._dep_resolution_cache: Dict[str, Dict[str, Any]] = {}
    
    def shellenv(self):
        """Prints the export command for the user's shell."""
        shell = os.environ.get("SHELL", "")
        profile = "~/.bashrc"
        if "zsh" in shell:
            profile = "~/.zshrc"
        elif "fish" in shell:
            profile = "~/.config/fish/config.fish"

        self.console.print(Panel(
            f"[bold green]Add this to your {profile}:[/bold green]\n\n"
            f'export PATH="{BIN_DIR}:$PATH"',
            title="Shell Configuration",
            border_style="green"
        ))
    
    def upgrade(self):
        """Upgrades all outdated packages."""
        outdated = []
        with self.console.status("[bold blue]Checking for updates..."):
            for name, info in self.inventory.items():
                data = self._get_api_data(name, force_refresh=True)  # Force refresh for upgrades
                if data and data['versions']['stable'] != info['version']:
                    outdated.append(name)
        
        if not outdated:
            self.console.print("[green]Everything is up to date![/green]")
            return

        self.console.print(f"[bold yellow]Upgrading: {', '.join(outdated)}[/bold yellow]")
        self.install(outdated, force=True)
    
    def doctor(self):
        issues = 0
        self.console.print("[bold]Running diagnostics...[/bold]")
        
        # Check the PATH if bin is stored
        if str(BIN_DIR) not in os.environ.get("PATH", ""):
            self.console.print(f"[red]![/red] Bin directory {BIN_DIR} is not in your PATH.")
            issues += 1
            
        for link in BIN_DIR.iterdir():
            if link.is_symlink() and not link.exists():
                self.console.print(f"[red]![/red] Broken symlink found: {link.name}")
                issues += 1

        # Check Inventory Consistency
        for name, info in self.inventory.items():
            if not Path(info['path']).exists():
                self.console.print(f"[red]![/red] Inventory says {name} is installed, but folder is missing.")
                issues += 1

        # Cache stats
        cache_stats = self.metadata_cache.stats()
        self.console.print(f"[cyan]Cache:[/cyan] {cache_stats['valid']} valid, {cache_stats['expired']} expired entries")
        
        if issues == 0:
            self.console.print("[green]Your system is healthy![/green]")
        else:
            self.console.print(f"[red]Found {issues} issues.[/red]")
    
    def cache_clear(self):
        """Clear the metadata cache."""
        with self.console.status("[bold yellow]Clearing cache..."):
            if CACHE_DB.exists():
                CACHE_DB.unlink()
                self.metadata_cache = MetadataCache(CACHE_DB)
            self.console.print("[green]✓ Cache cleared![/green]")
    
    def cache_stats(self):
        """Display cache statistics."""
        stats = self.metadata_cache.stats()
        
        table = Table(title="Cache Statistics", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Total Entries", str(stats['total']))
        table.add_row("Valid Entries", str(stats['valid']))
        table.add_row("Expired Entries", str(stats['expired']))
        
        cache_size = CACHE_DB.stat().st_size / 1024 if CACHE_DB.exists() else 0
        table.add_row("Cache Size", f"{cache_size:.2f} KB")
        
        self.console.print(table)

    def log(self, message: str):
        if self.verbose:
            self.console.print(f"[dim]DEBUG: {message}[/dim]")

    def _save_inventory(self) -> None:
        """Save inventory with file locking."""
        with open(INVENTORY_FILE, 'w') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(self.inventory, f, indent=4)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    @lru_cache(maxsize=128)
    def _verify_sha256(self, file_path: str, expected_sha: str) -> bool:
        """Verify SHA256 with LRU cache for repeated checks."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(65536), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest() == expected_sha

    def _get_api_data(self, pkg_name: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """Fetch package metadata with caching and retry logic."""
        # Check cache first unless force refresh
        if not force_refresh:
            cached = self.metadata_cache.get(pkg_name)
            if cached:
                self.log(f"Cache hit for {pkg_name}")
                return cached
        
        url = f"https://formulae.brew.sh/api/formula/{pkg_name}.json"
        
        for attempt in range(RETRY_ATTEMPTS):
            try:
                self.log(f"Fetching metadata for {pkg_name} (attempt {attempt + 1}/{RETRY_ATTEMPTS})")
                resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                
                if resp.status_code == 200:
                    data = resp.json()
                    # Cache the successful response
                    self.metadata_cache.set(pkg_name, data)
                    return data
                elif resp.status_code == 404:
                    return None
                else:
                    self.log(f"HTTP {resp.status_code} for {pkg_name}")
                    
            except requests.Timeout:
                self.log(f"Timeout fetching {pkg_name}")
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
            except Exception as e:
                self.log(f"API Error for {pkg_name}: {e}")
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
        
        return None

    def search(self, query: str) -> None:
        with self.console.status(f"[bold green]Searching for {query}...", spinner="dots"):
            data = self._get_api_data(query)
        
        if data:
            self.console.print(f"++ [bold green]{data['name']}[/bold green] v{data['versions']['stable']}")
            self.console.print(f"   {data['desc']}")
        else:
            self.console.print(f"[red]✗[/red] No exact match for '{query}'")

    def info(self, pkg_name: str) -> None:
        with self.console.status(f"[bold blue]Fetching info for {pkg_name}..."):
            data = self._get_api_data(pkg_name)
        
        if not data:
            self.console.print(f"[red]✗[/red] Package '{pkg_name}' not found.")
            return

        status = "[green]Installed[/green]" if pkg_name in self.inventory else "[red]Not Installed[/red]"
        deps = ", ".join(data.get('dependencies', [])) or "None"
        
        info_panel = Panel(
            f"[bold cyan]Description:[/bold cyan] {data['desc']}\n"
            f"[bold cyan]Homepage:[/bold cyan] [underline]{data['homepage']}[/underline]\n"
            f"[bold cyan]Latest Version:[/bold cyan] {data['versions']['stable']}\n"
            f"[bold cyan]Status:[/bold cyan] {status}\n\n"
            f"[bold magenta]Dependencies:[/bold magenta] {deps}",
            title=f"[||]  {data['name']}",
            expand=False,
            border_style="blue"
        )
        self.console.print(info_panel)

    def list_installed(self) -> None:
        if not self.inventory:
            self.console.print("[yellow]Your Cellar is empty.[/yellow]")
            return

        table = Table(title="Installed Packages", box=box.ROUNDED, header_style="bold magenta")
        table.add_column("Package", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Path", style="dim")

        for name, info in self.inventory.items():
            table.add_row(name, info['version'], info['path'])
        
        self.console.print(table)

    def check_outdated(self) -> None:
        table = Table(title="Updates Available", box=box.SIMPLE)
        table.add_column("Package", style="cyan")
        table.add_column("Current", style="red")
        table.add_column("Latest", style="green")

        found: bool = False
        # Fetcg the data using threads, for better performance...
        with self.console.status("[bold blue]Checking for updates..."):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_pkg = {
                    executor.submit(self._get_api_data, name, True): name 
                    for name in self.inventory.keys()
                }
                
                for future in concurrent.futures.as_completed(future_to_pkg):
                    name = future_to_pkg[future]
                    try:
                        data = future.result()
                        if data:
                            current_ver = self.inventory[name]['version']
                            latest_ver = data['versions']['stable']
                            if latest_ver != current_ver:
                                table.add_row(name, current_ver, latest_ver)
                                found = True
                    except Exception as e:
                        self.log(f"Error checking {name}: {e}")
        
        if found:
            self.console.print(table)
        else:
            self.console.print("[green]✓ All packages are up to date![/green]")

    def cleanup(self) -> None:
        self.console.print("[bold yellow]\\[ ] Cleaning up...[/bold yellow]")
        bytes_saved = 0
        
        # Clean old tar-balls
        for tmp_file in BASE_DIR.glob("*.tar.gz"):
            bytes_saved += tmp_file.stat().st_size
            tmp_file.unlink()
        
        # Clean old versions of 
        for pkg_folder in CELLAR.iterdir():
            if pkg_folder.is_dir():
                active_ver = self.inventory.get(pkg_folder.name, {}).get('version')
                for ver_folder in pkg_folder.iterdir():
                    if ver_folder.is_dir() and ver_folder.name != active_ver:
                        folder_size = sum(f.stat().st_size for f in ver_folder.rglob('*') if f.is_file())
                        bytes_saved += folder_size
                        shutil.rmtree(ver_folder)
        
        expired_count = self.metadata_cache.clear_expired()
        self.console.print(f"[green]✓ Cleanup complete! Freed {bytes_saved / (1024*1024):.2f} MB[/green]")
        self.console.print(f"[green]✓ Removed {expired_count} expired cache entries[/green]")

    def _resolve_graph(self, pkg_name: str, parent_name: str, res_map: Dict[str, Dict[str, Any]]):
        """Resolve dependency graph with session-level caching."""
        if pkg_name in self._dep_resolution_cache:
            cached_result = self._dep_resolution_cache[pkg_name]
            if pkg_name not in res_map:
                res_map[pkg_name] = cached_result
                # Recursively add cached dependencies
                for dep in cached_result.get('dependencies', []):
                    if dep in self._dep_resolution_cache and dep not in res_map:
                        res_map[dep] = self._dep_resolution_cache[dep]
            return
        
        data = self._get_api_data(pkg_name)
        if not data:
            raise Exception(f"Metadata missing for: {pkg_name}")

        current_ver: str = data['versions']['stable']
        if pkg_name in res_map:
            return

        result = {
            "version": current_ver, 
            "requested_by": parent_name,
            "dependencies": data.get('dependencies', [])
        }
        
        res_map[pkg_name] = result
        self._dep_resolution_cache[pkg_name] = result
        
        for dep in data.get('dependencies', []):
            self._resolve_graph(dep, pkg_name, res_map)

    def install(self, pkg_names: List[str], force: bool = False) -> None:
        resolution_map = {}
        with self.console.status("[bold blue]Resolving dependencies..."):
            try:
                for pkg in pkg_names:
                    self._resolve_graph(pkg, "User Request", resolution_map)
            except Exception as e:
                self.console.print(f"[bold red]Resolution Error:[/bold red] {e}")
                return

        if force:
            to_fetch = resolution_map
        else:
            to_fetch = {name: d for name, d in resolution_map.items() if name not in self.inventory}

        if not to_fetch:
            self.console.print("[green]- All requested packages are already installed. Use --force to reinstall.[/green]")
            return

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
        )

        with progress:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL_DOWNLOADS) as executor:
                futures = []
                for name, details in to_fetch.items():
                    task_id = progress.add_task(f"Installing {name}...", total=None)
                    futures.append(executor.submit(
                        self._download_and_extract_worker, 
                        name, 
                        details['version'], 
                        task_id, 
                        progress
                    ))

                for future in concurrent.futures.as_completed(futures):
                    try:
                        res = future.result()
                        if res:
                            pkg, version, pkg_dir, bin_links = res
                            self.inventory[pkg] = {
                                "version": version, 
                                "path": str(pkg_dir), 
                                "symlinks": bin_links
                            }
                            self._save_inventory()
                    except Exception as e:
                        self.console.print(f"[red]Error during install:[/red] {e}")

    def _download_and_extract_worker(self, pkg: str, version: str, task_id, progress):
        """Download and extract with improved error handling and verification."""
        try:
            data = self._get_api_data(pkg)
            if not data:
                progress.update(task_id, description=f"[red]Metadata not found for {pkg}[/red]")
                return None
                
            bottle = data['bottle']['stable']['files'].get(OS_FLAVOR)
            if not bottle:
                progress.update(task_id, description=f"[yellow]No bottle for {OS_FLAVOR}, skipping {pkg}[/yellow]")
                return None

            # Auth with GHCR
            token_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:homebrew/core/{pkg}:pull"
            token_resp = requests.get(token_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            token = token_resp.json().get('token')
            
            tmp_file = BASE_DIR / f"{pkg}_{version}.tar.gz"
            
            # Download with retries in case net is slow
            for attempt in range(RETRY_ATTEMPTS):
                try:
                    with requests.get(
                        bottle['url'], 
                        headers={'Authorization': f'Bearer {token}'}, 
                        stream=True,
                        timeout=REQUEST_TIMEOUT
                    ) as r:
                        r.raise_for_status()
                        total_size = int(r.headers.get('content-length', 0))
                        progress.update(task_id, total=total_size)
                        
                        with open(tmp_file, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                                progress.update(task_id, advance=len(chunk))
                        break
                except requests.RequestException as e:
                    if attempt < RETRY_ATTEMPTS - 1:
                        progress.update(task_id, description=f"[yellow]Retry {attempt + 1} for {pkg}[/yellow]")
                        time.sleep(RETRY_DELAY)
                    else:
                        raise

            progress.update(task_id, description=f"[blue]Verifying {pkg}...[/blue]")
            if not self._verify_sha256(str(tmp_file), bottle['sha256']):
                progress.update(task_id, description=f"[red]SHA mismatch {pkg}[/red]")
                tmp_file.unlink()
                raise Exception(f"SHA256 mismatch for {pkg}")

            progress.update(task_id, description=f"[blue]Extracting {pkg}...[/blue]")
            final_pkg_dir = CELLAR / pkg / version
            if final_pkg_dir.exists():
                shutil.rmtree(final_pkg_dir)
            final_pkg_dir.mkdir(parents=True, exist_ok=True)
            
            # Extract to temp first to handle nesting issues
            with tempfile.TemporaryDirectory() as temp_extract_dir:
                with tarfile.open(tmp_file, "r:gz") as tar:
                    tar.extractall(path=temp_extract_dir)
                
                extracted_root = Path(temp_extract_dir)
                if (extracted_root / pkg / version).exists():
                    source_dir = extracted_root / pkg / version
                elif (extracted_root / pkg).exists():
                    source_dir = extracted_root / pkg
                else:
                    source_dir = extracted_root
                
                for item in source_dir.iterdir():
                    shutil.move(str(item), str(final_pkg_dir))

            tmp_file.unlink()

            # Link binaries
            bin_links = []
            for bin_folder_name in ["bin", "sbin"]:
                bin_path = final_pkg_dir / bin_folder_name
                if bin_path.exists():
                    for exe in bin_path.iterdir():
                        if exe.is_file():
                            exe.chmod(exe.stat().st_mode | 0o111)
                            
                            link_dest = BIN_DIR / exe.name
                            if link_dest.exists() or link_dest.is_symlink():
                                link_dest.unlink()
                            link_dest.symlink_to(exe)
                            bin_links.append(str(link_dest))
            
            progress.update(task_id, description=f"[green]✓ Installed {pkg}[/green]")
            return pkg, version, final_pkg_dir, bin_links
            
        except Exception as e:
            progress.update(task_id, description=f"[red]✗ Failed {pkg}: {str(e)[:30]}[/red]")
            raise

    def uninstall(self, pkg_names: List[str], confirm: bool = True) -> None:
        to_remove = [p for p in pkg_names if p in self.inventory]
        
        if not to_remove:
            self.console.print(f"[yellow]Packages not found: {pkg_names}[/yellow]")
            return

        if confirm:
            if not Confirm.ask(f"Uninstall {', '.join(to_remove)}?"):
                return

        with self.console.status(f"[bold red]Uninstalling..."):
            for pkg_name in to_remove:
                info = self.inventory[pkg_name]
                for link in info['symlinks']:
                    p = Path(link)
                    if p.exists() or p.is_symlink():
                        p.unlink()
                
                shutil.rmtree(CELLAR / pkg_name, ignore_errors=True)
                
                # Invalidate cache for uninstalled package
                self.metadata_cache.invalidate(pkg_name)
                
                del self.inventory[pkg_name]
                self._save_inventory()
                self.console.print(f"[green]✓ Uninstalled {pkg_name}[/green]")


def main():
    shared_args = argparse.ArgumentParser(add_help=False)
    shared_args.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    parser = argparse.ArgumentParser(
        prog="br",
        parents=[shared_args],
        description="A lightweight, Python-based Package Manager",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    p_install = subparsers.add_parser("install", parents=[shared_args], conflict_handler='resolve', help="Install packages")
    p_install.add_argument("packages", nargs="+", help="Package names")
    p_install.add_argument("-f", "--force", action="store_true", help="Force reinstall")

    p_uninstall = subparsers.add_parser("uninstall", parents=[shared_args], conflict_handler='resolve', help="Uninstall packages")
    p_uninstall.add_argument("packages", nargs="+")
    p_uninstall.add_argument("-y", "--yes", action="store_true", help="Skip confirm")

    p_search = subparsers.add_parser("search", parents=[shared_args], conflict_handler='resolve')
    p_search.add_argument("query")

    p_info = subparsers.add_parser("info", parents=[shared_args], conflict_handler='resolve')
    p_info.add_argument("package")

    subparsers.add_parser("list", parents=[shared_args], conflict_handler='resolve')
    subparsers.add_parser("outdated", parents=[shared_args], conflict_handler='resolve')
    subparsers.add_parser("cleanup", parents=[shared_args], conflict_handler='resolve')
    subparsers.add_parser("upgrade", parents=[shared_args], conflict_handler='resolve', help="Upgrade outdated packages")
    subparsers.add_parser("doctor", parents=[shared_args], conflict_handler='resolve', help="Check system health")
    subparsers.add_parser("shellenv", parents=[shared_args], conflict_handler='resolve', help="Display shell configuration")
    
    subparsers.add_parser("cache-clear", parents=[shared_args], conflict_handler='resolve', help="Clear metadata cache")
    subparsers.add_parser("cache-stats", parents=[shared_args], conflict_handler='resolve', help="Show cache statistics")
    
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    brew = Brewery(verbose=args.verbose)

    if args.command == "install":
        brew.install(args.packages, force=args.force)
    elif args.command == "uninstall":
        brew.uninstall(args.packages, confirm=not args.yes)
    elif args.command == "search":
        brew.search(args.query)
    elif args.command == "info":
        brew.info(args.package)
    elif args.command == "list":
        brew.list_installed()
    elif args.command == "outdated":
        brew.check_outdated()
    elif args.command == "cleanup":
        brew.cleanup()
    elif args.command == "shellenv":
        brew.shellenv()
    elif args.command == "upgrade":
        brew.upgrade()
    elif args.command == "doctor":
        brew.doctor()
    elif args.command == "cache-clear":
        brew.cache_clear()
    elif args.command == "cache-stats":
        brew.cache_stats()

if __name__ == "__main__":
    main()
