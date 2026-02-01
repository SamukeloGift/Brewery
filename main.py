#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rich",
#     "requests",
# ]
# ///
import os
from typing import Optional
import json

import hashlib
import requests
import tarfile
import shutil
import sys
import platform
import concurrent.futures
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn
from rich.panel import Panel
# from rich.live import Live
from rich import box

BASE_DIR = Path.home() / ".br"
CELLAR = BASE_DIR / "Cellar"
BIN_DIR = BASE_DIR / "bin"
INVENTORY_FILE = BASE_DIR / "inventory.json"

OS_MAP: dict[str, str] = {
    "26": "tahoe",
    "15": "sequoia",
    "14": "sonoma",
    "13": "ventura",
    "12": "monterey",
    "11": "big_sur",
    "10.15": "catalina",
    "10.14": "mojave",
    "10.13": "high_sierra"
}

def get_os_flavor() -> str:
    """Detects architecture and macos version to return the OS flavor string.
    This Tries to MATCH Brew's Naming convention for bottle downloads.

    """
    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    mac_ver: str = platform.mac_ver()[0]
    major_ver: str = mac_ver.split(".")[0]
    
    name:str = OS_MAP.get(major_ver) or "sonoma" # Will Fallback to Sonoma if OS DOESN'T exist
    return f"{arch}_{name}"

# OS_FLAVOR = get_os_flavor()
OS_FLAVOR = "sonoma"  # For testing on non-mac systems
MAX_PARALLEL_DOWNLOADS = 5

class Brewery:
    def __init__(self)->None:
        self.console = Console()
        for folder in [CELLAR, BIN_DIR]:
            folder.mkdir(parents=True, exist_ok=True)
        
        # Load The metadata DB
        if INVENTORY_FILE.exists():
            with open(INVENTORY_FILE, 'r') as f:
                self.inventory = json.load(f)
        else:
            self.inventory = {}

    def _save_inventory(self)-> Optional[None]:
        with open(INVENTORY_FILE, 'w') as f:
            json.dump(self.inventory, f, indent=4)
    def _verify_sha256(self, file_path: Path, expected_sha: str) -> bool:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read in 64kb chunks
            for byte_block in iter(lambda: f.read(65536), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest() == expected_sha
    def _get_api_data(self, pkg_name: str):
        url:str = f"https://formulae.brew.sh/api/formula/{pkg_name}.json"
        try:
            resp = requests.get(url, timeout=10)
            return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None


    def search(self, query: str)-> None:
        with self.console.status(f"[bold green]Searching for {query}...", spinner="dots"):
            data = self._get_api_data(query)
        
        if data:
            self.console.print(f"++ [bold green]{data['name']}[/bold green] v{data['versions']['stable']}")
            self.console.print(f"   {data['desc']}")
        else:
            self.console.print(f"[red]✗[/red] No exact match for '{query}'")

    def info(self, pkg_name: str)-> None:
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

    def list_installed(self)-> None:
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

    def check_outdated(self)->None:
        table = Table(title="Updates Available", box=box.SIMPLE)
        table.add_column("Package", style="cyan")
        table.add_column("Current", style="red")
        table.add_column("Latest", style="green")

        found: bool = False
        with self.console.status("[bold blue]Checking for updates..."):
            for name, info in self.inventory.items():
                data = self._get_api_data(name)
                if data and data['versions']['stable'] != info['version']:
                    table.add_row(name, info['version'], data['versions']['stable'])
                    found = True
        
        if found:
            self.console.print(table)
        else:
            self.console.print("[green]✓ All packages are up to date![/green]")

    def cleanup(self):
        self.console.print("[bold yellow] \\[ ] Cleaning up...[/bold yellow]")
        bytes_saved = 0
        for tmp_file in BASE_DIR.glob("*.tar.gz"):
            bytes_saved += tmp_file.stat().st_size
            tmp_file.unlink()
        
        for pkg_folder in CELLAR.iterdir():
            if pkg_folder.is_dir():
                active_ver = self.inventory.get(pkg_folder.name, {}).get('version')
                for ver_folder in pkg_folder.iterdir():
                    if ver_folder.is_dir() and ver_folder.name != active_ver:
                        folder_size = sum(f.stat().st_size for f in ver_folder.rglob('*') if f.is_file())
                        bytes_saved += folder_size
                        shutil.rmtree(ver_folder)
        
        self.console.print(f"[green]✓ Cleanup complete! Freed {bytes_saved / (1024*1024):.2f} MB[/green]")


    def _resolve_graph(self, pkg_name, parent_name, res_map):
        # if sel
        data = self._get_api_data(pkg_name)
        if not data: raise Exception(f"Metadata missing for: {pkg_name}")

        current_ver:str = data['versions']['stable']
        if pkg_name in res_map: return

        res_map[pkg_name] = {"version": current_ver, "requested_by": parent_name}
        for dep in data.get('dependencies', []):
            self._resolve_graph(dep, pkg_name, res_map)

    def install(self, pkg_names):
        resolution_map = {}
        with self.console.status("[bold blue]Resolving dependencies..."):
            try:
                for pkg in pkg_names:
                    self._resolve_graph(pkg, "User Request", resolution_map)
            except Exception as e:
                self.console.print(f"[bold red]Resolution Error:[/bold red] {e}")
                return

        to_fetch = {name: d for name, d in resolution_map.items() if name not in self.inventory}
        if not to_fetch:
            self.console.print("[green]- All requested packages are already installed.[/green]")
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
                    futures.append(executor.submit(self._download_and_extract_worker, name, details['version'], task_id, progress))

                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self.console.print(f"[red]Error during install:[/red] {e}")

    def _download_and_extract_worker(self, pkg, version, task_id, progress):
        data = self._get_api_data(pkg)
        bottle = data['bottle']['stable']['files'].get(OS_FLAVOR)
        if not bottle:
            progress.update(task_id, description=f"[yellow]No bottle for {OS_FLAVOR}, skipping {pkg}[/yellow]")
            return

        token_url:str= f"https://ghcr.io/token?service=ghcr.io&scope=repository:homebrew/core/{pkg}:pull"
        token = requests.get(token_url).json().get('token')
        tmp_file:Path = BASE_DIR / f"{pkg}_{version}.tar.gz"
        
        with requests.get(bottle['url'], headers={'Authorization': f'Bearer {token}'}, stream=True) as r:
            total_size = int(r.headers.get('content-length', 0))
            progress.update(task_id, total=total_size)
            with open(tmp_file, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task_id, advance=len(chunk))

        progress.update(task_id, description=f"[blue]Extracting {pkg}...[/blue]")
        if not self._verify_sha256(tmp_file, bottle['sha256']):
            self.console.print(f"[red]✗ SHA256 mismatch for {pkg}, aborting installation.[/red]")
            tmp_file.unlink()
            progress.update(task_id, description=f"[red]✗ Package: {pkg} has incorrect SHA256[/red]")
            return
        pkg_dir = CELLAR / pkg / version
        pkg_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tmp_file, "r:gz") as tar:
            tar.extractall(path=pkg_dir)
        tmp_file.unlink()

        bin_links = []
        for root, dirs, files in os.walk(pkg_dir):
            if "bin" in dirs:
                bin_path = Path(root) / "bin"
                for exe in bin_path.iterdir():
                    link_dest = BIN_DIR / exe.name
                    if not link_dest.exists():
                        link_dest.symlink_to(exe)
                        bin_links.append(str(link_dest))
        
        self.inventory[pkg] = {"version": version, "path": str(pkg_dir), "symlinks": bin_links}
        self._save_inventory()
        progress.update(task_id, description=f"[green]✓ Installed {pkg}[/green]")

    def uninstall(self, pkg_name):
        if pkg_name not in self.inventory:
            self.console.print(f"[red]✗[/red] {pkg_name} is not installed.")
            return

        with self.console.status(f"[bold red]Uninstalling {pkg_name}..."):
            info = self.inventory[pkg_name]
            for link in info['symlinks']:
                p = Path(link)
                if p.exists(): p.unlink()
            shutil.rmtree(CELLAR / pkg_name)
            del self.inventory[pkg_name]
            self._save_inventory()
        
        self.console.print(f"[green]✓ Successfully uninstalled {pkg_name}[/green]")

if __name__ == "__main__":
    brew = Brewery()
    if len(sys.argv) < 2:
        brew.console.print(Panel("[bold blue]brPackage Manager[/bold blue]\nUsage: br [list|outdated|search|info|install|uninstall|cleanup] <package>", border_style="cyan"))
        sys.exit(0)

    cmd, targets = sys.argv[1], sys.argv[2:]

    if cmd == "list": brew.list_installed()
    elif cmd == "outdated": brew.check_outdated()
    elif cmd == "cleanup": brew.cleanup()
    elif cmd == "search" and targets: brew.search(targets[0])
    elif cmd == "info" and targets: brew.info(targets[0])
    elif cmd == "install" and targets: brew.install(targets)
    elif cmd == "uninstall" and targets:
        for t in targets: brew.uninstall(t)
    else:
        brew.console.print("[red]Invalid command or missing arguments.[/red]")
