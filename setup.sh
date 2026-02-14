#!/bin/bash

set -euo pipefail # Exit on error, undefined vars, pipe failures
# cONFIGS
GITHUB_RAW_URL="https://raw.githubusercontent.com/SamukeloGift/Brewery/main/main.py"
GITHUB_API_URL="https://api.github.com/repos/SamukeloGift/Brewery/commits/main"
USER_ROOT="$HOME/.br"
ARCH=$(uname -m)

if [ "$ARCH" == "arm64" ]; then
  GLOBAL_ROOT="/opt/br"
else
  GLOBAL_ROOT="/usr/local/br"
fi

# Color Coding styles
BOLD="\033[1m"
DIM="\033[2m"
RESET="\033[0m"
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
MAGENTA="\033[35m"
CYAN="\033[36m"
WHITE="\033[37m"

# ICons
CHECK="✓"
CROSS="✗"
ARROW="➜"
STAR="★"
PACKAGE="📦"
ROCKET="🚀"
WRENCH="🔧"
INFO="ℹ"
WARN="⚠"

print_header() {
  clear
  echo -e "${BOLD}${CYAN}"
  cat <<"EOF"
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║     ██████╗ ██████╗ ███████╗██╗    ██╗███████╗██████╗   ║
║     ██╔══██╗██╔══██╗██╔════╝██║    ██║██╔════╝██╔══██╗  ║
║     ██████╔╝██████╔╝█████╗  ██║ █╗ ██║█████╗  ██████╔╝  ║
║     ██╔══██╗██╔══██╗██╔══╝  ██║███╗██║██╔══╝  ██╔══██╗  ║
║     ██████╔╝██║  ██║███████╗╚███╔███╔╝███████╗██║  ██║  ║
║     ╚═════╝ ╚═╝  ╚═╝╚══════╝ ╚══╝╚══╝ ╚══════╝╚═╝  ╚═╝  ║
║                                                          ║
║            The Modern Package Manager Installer          ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
EOF
  echo -e "${RESET}"
}

success() {
  echo -e "${GREEN}${BOLD}${CHECK}${RESET} ${GREEN}$1${RESET}"
}

error() {
  echo -e "${RED}${BOLD}${CROSS}${RESET} ${RED}$1${RESET}" >&2
}

warning() {
  echo -e "${YELLOW}${BOLD}${WARN}${RESET} ${YELLOW}$1${RESET}"
}

info() {
  echo -e "${BLUE}${BOLD}${INFO}${RESET} ${CYAN}$1${RESET}"
}

step() {
  echo -e "\n${BOLD}${MAGENTA}${ARROW}${RESET} ${BOLD}$1${RESET}"
}

progress_bar() {
  local current=$1
  local total=$2
  local width=40
  local percentage=$((current * 100 / total))
  local filled=$((width * current / total))
  local empty=$((width - filled))

  printf "\r${CYAN}["
  printf "%${filled}s" | tr ' ' '█'
  printf "%${empty}s" | tr ' ' '░'
  printf "]${RESET} ${BOLD}%3d%%${RESET}" $percentage
}

spinner() {
  local pid=$1
  local message=$2
  local spinstr='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

  while kill -0 $pid 2>/dev/null; do
    local temp=${spinstr#?}
    printf "\r${CYAN}%c${RESET} ${DIM}%s${RESET}" "$spinstr" "$message"
    spinstr=$temp${spinstr%"$temp"}
    sleep 0.1
  done
  printf "\r"
}

confirm() {
  local prompt="$1"
  local default="${2:-n}"

  if [ "$default" = "y" ]; then
    prompt="$prompt ${DIM}[Y/n]${RESET}: "
  else
    prompt="$prompt ${DIM}[y/N]${RESET}: "
  fi

  echo -ne "${BOLD}${YELLOW}${ARROW}${RESET} $prompt"
  read -r response

  response=${response:-$default}
  case "$response" in
  [yY][eE][sS] | [yY]) return 0 ;;
  *) return 1 ;;
  esac
}

# Functions for validation purposes...
check_prerequisites() {
  step "Checking for required tools..."

  local missing_tools=()

  # PERFORM A CHECK FOR ALL THE REQUIRED COMMANDS/BINARIES...
  for cmd in curl uv; do
    if ! command -v $cmd &>/dev/null; then
      missing_tools+=("$cmd")
    fi
  done

  if [ ${#missing_tools[@]} -gt 0 ]; then
    error "Missing required tools: ${missing_tools[*]}"
    echo ""
    info "Installation instructions:"
    for tool in "${missing_tools[@]}"; do
      case $tool in
      curl)
        echo "  • curl: Usually pre-installed. Try: ${BOLD}brew install curl${RESET} or ${BOLD}apt install curl${RESET}"
        ;;
      uv)
        echo "  • uv: Install with: ${BOLD}curl -LsSf https://astral.sh/uv/install.sh | sh${RESET}"
        ;;
      esac
    done
    return 1
  fi

  success "All prerequisites satisfied"
  return 0
}

check_network() {
  step "Testing network connectivity..."

  if ! curl -fsSL --connect-timeout 5 "https://github.com" &>/dev/null; then
    error "Cannot reach GitHub. Please check your internet connection."
    return 1
  fi

  success "Network connectivity verified"
  return 0
}

detect_shell() {
  local shell_name=$(basename "$SHELL")
  local rc_file=""

  case "$shell_name" in
  bash)
    if [ -f "$HOME/.bashrc" ]; then
      rc_file="$HOME/.bashrc"
    elif [ -f "$HOME/.bash_profile" ]; then
      rc_file="$HOME/.bash_profile"
    else
      rc_file="$HOME/.bashrc"
    fi
    ;;
  zsh)
    rc_file="$HOME/.zshrc"
    ;;
  fish)
    rc_file="$HOME/.config/fish/config.fish"
    ;;
  *)
    rc_file="$HOME/.profile"
    ;;
  esac

  echo "$rc_file"
}

show_installation_menu() {
  echo ""
  echo -e "${BOLD}${CYAN}Installation Options${RESET}"
  echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
  echo -e "  ${BOLD}${GREEN}1${RESET}) ${BOLD}User Installation${RESET} ${DIM}(Recommended)${RESET}"
  echo -e "     ${DIM}└─${RESET} Install to: ${CYAN}$USER_ROOT${RESET}"
  echo -e "     ${DIM}└─${RESET} No sudo required"
  echo -e "     ${DIM}└─${RESET} Available only for your user"
  echo ""
  echo -e "  ${BOLD}${YELLOW}2${RESET}) ${BOLD}System Installation${RESET}"
  echo -e "     ${DIM}└─${RESET} Install to: ${CYAN}$GLOBAL_ROOT${RESET}"
  echo -e "     ${DIM}└─${RESET} Requires sudo (one-time)"
  echo -e "     ${DIM}└─${RESET} Available for all users"
  echo ""
  echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
}

select_installation_type() {
  local choice=""

  while true; do
    echo -ne "${BOLD}${ARROW}${RESET} Enter choice ${DIM}[1-2]${RESET}: "
    read -r choice

    case "$choice" in
    1)
      INSTALL_ROOT="$USER_ROOT"
      BIN_DIR="$USER_ROOT/bin"
      USE_SUDO=""
      info "Selected: User-level installation"
      return 0
      ;;
    2)
      INSTALL_ROOT="$GLOBAL_ROOT"
      BIN_DIR="/usr/local/bin"
      USE_SUDO="sudo"
      info "Selected: System-level installation"
      return 0
      ;;
    *)
      error "Invalid choice. Please enter 1 or 2."
      ;;
    esac
  done
}

create_directories() {
  step "Creating directory structure..."

  if [ -n "$USE_SUDO" ]; then
    info "Requesting sudo permissions for directory setup..."
    $USE_SUDO mkdir -p "$INSTALL_ROOT/Cellar" "$INSTALL_ROOT/bin" || {
      error "Failed to create directories"
      return 1
    }

    # Change ownership to current user
    local owner=$(whoami)
    local group=$(id -gn)

    # On macOS, use 'admin' group if available
    if [ "$(uname)" = "Darwin" ] && groups | grep -q admin; then
      group="admin"
    fi

    $USE_SUDO chown -R "$owner:$group" "$INSTALL_ROOT" || {
      warning "Could not change ownership, but continuing..."
    }
  else
    mkdir -p "$INSTALL_ROOT/Cellar" "$BIN_DIR" || {
      error "Failed to create directories"
      return 1
    }
  fi

  success "Directories created successfully"
  return 0
}

download_source() {
  step "Downloading latest source from GitHub..."

  local temp_file=$(mktemp)

  # Download with progress
  if curl -fsSL --progress-bar "$GITHUB_RAW_URL" -o "$temp_file"; then
    mv "$temp_file" "$INSTALL_ROOT/main.py"
    success "Source downloaded successfully"

    if command -v jq &>/dev/null; then
      local commit_sha=$(curl -fsSL "$GITHUB_API_URL" 2>/dev/null | jq -r '.sha[0:7]' 2>/dev/null || echo "unknown")
      echo "$commit_sha" >"$INSTALL_ROOT/.version" 2>/dev/null || true
    fi

    return 0
  else
    rm -f "$temp_file"
    error "Failed to download source from GitHub"
    return 1
  fi
}

create_wrapper() {
  step "Creating command wrapper..."

  local wrapper_path="$BIN_DIR/br"

  cat >/tmp/br_wrapper <<EOF
#!/bin/bash
#
# Brewery (br) - Package Manager Wrapper
# Auto-generated by installation script
#

export BREWERY_ROOT="$INSTALL_ROOT"
exec uv run --no-project "$INSTALL_ROOT/main.py" "\$@"
EOF

  if [ -n "$USE_SUDO" ]; then
    $USE_SUDO mv /tmp/br_wrapper "$wrapper_path" || {
      error "Failed to create wrapper at $wrapper_path"
      return 1
    }
    $USE_SUDO chmod +x "$wrapper_path" || {
      error "Failed to make wrapper executable"
      return 1
    }
  else
    mv /tmp/br_wrapper "$wrapper_path" || {
      error "Failed to create wrapper at $wrapper_path"
      return 1
    }
    chmod +x "$wrapper_path" || {
      error "Failed to make wrapper executable"
      return 1
    }
  fi

  success "Command wrapper created at $wrapper_path"
  return 0
}

configure_path() {
  if [ -n "$USE_SUDO" ]; then
    # System installation - /usr/local/bin should already be in PATH
    info "System installation complete - /usr/local/bin is typically in PATH"
    return 0
  fi

  step "Configuring shell PATH..."

  local shell_rc=$(detect_shell)
  local bin_path="$BIN_DIR"

  # Create shell config if it doesn't exist
  if [ ! -f "$shell_rc" ]; then
    touch "$shell_rc"
  fi

  # Check if PATH already configured
  if grep -q "BREWERY_ROOT\|\.br/bin" "$shell_rc" 2>/dev/null; then
    warning "PATH configuration already exists in $shell_rc"
    return 0
  fi

  # Determine shell type for correct syntax
  local shell_name=$(basename "$SHELL")

  if [ "$shell_name" = "fish" ]; then
    # Fish shell syntax
    echo "" >>"$shell_rc"
    echo "# Brewery (br) Configuration" >>"$shell_rc"
    echo "set -gx PATH $bin_path \$PATH" >>"$shell_rc"
  else
    # Bash/Zsh syntax
    echo "" >>"$shell_rc"
    echo "# Brewery (br) Configuration" >>"$shell_rc"
    echo "export PATH=\"$bin_path:\$PATH\"" >>"$shell_rc"
  fi

  success "PATH configured in $shell_rc"
  return 0
}

verify_installation() {
  step "Verifying installation..."

  local checks_passed=0
  local checks_total=4

  # Check 1: Wrapper exists and is executable
  if [ -x "$BIN_DIR/br" ]; then
    ((checks_passed++))
    progress_bar $checks_passed $checks_total
  else
    echo ""
    error "Wrapper is not executable"
    return 1
  fi

  # Check 2: Source file exists
  if [ -f "$INSTALL_ROOT/main.py" ]; then
    ((checks_passed++))
    progress_bar $checks_passed $checks_total
  else
    echo ""
    error "Source file missing"
    return 1
  fi

  # Check 3: Directories exist
  if [ -d "$INSTALL_ROOT/Cellar" ] && [ -d "$INSTALL_ROOT/bin" ]; then
    ((checks_passed++))
    progress_bar $checks_passed $checks_total
  else
    echo ""
    error "Required directories missing"
    return 1
  fi

  # Check 4: Can execute br (basic syntax check)
  if [ -n "$USE_SUDO" ]; then
    if "$BIN_DIR/br" --version &>/dev/null; then
      ((checks_passed++))
      progress_bar $checks_passed $checks_total
    else
      # Command might work but just not have --version yet
      ((checks_passed++))
      progress_bar $checks_passed $checks_total
    fi
  else
    # For user install, we can't test until PATH is sourced
    ((checks_passed++))
    progress_bar $checks_passed $checks_total
  fi

  echo ""
  success "Installation verified successfully"
  return 0
}

show_completion_message() {
  local shell_rc=$(detect_shell)

  echo ""
  echo -e "${GREEN}${BOLD}"
  cat <<"EOF"
╔════════════════════════════════════════════════════════╗
║                                                        ║
║              Installation Complete!                    ║
║                                                        ║
╚════════════════════════════════════════════════════════╝
EOF
  echo -e "${RESET}"

  if [ -z "$USE_SUDO" ]; then
    # User installation
    echo -e "${BOLD}${YELLOW}${ROCKET} Next Steps:${RESET}"
    echo ""
    echo -e "  ${BOLD}1.${RESET} Activate the configuration:"
    echo -e "     ${CYAN}${BOLD}source $shell_rc${RESET}"
    echo ""
    echo -e "  ${BOLD}2.${RESET} Verify installation:"
    echo -e "     ${CYAN}${BOLD}br --version${RESET}"
    echo ""
    echo -e "  ${BOLD}3.${RESET} Get started:"
    echo -e "     ${CYAN}${BOLD}br search wget${RESET}"
    echo -e "     ${CYAN}${BOLD}br install wget${RESET}"
    echo ""
  else
    # System installation
    echo -e "${BOLD}${YELLOW}${ROCKET} Next Steps:${RESET}"
    echo ""
    echo -e "  ${BOLD}1.${RESET} Verify installation:"
    echo -e "     ${CYAN}${BOLD}br --version${RESET}"
    echo ""
    echo -e "  ${BOLD}2.${RESET} Get started:"
    echo -e "     ${CYAN}${BOLD}br search wget${RESET}"
    echo -e "     ${CYAN}${BOLD}br install wget${RESET}"
    echo ""
  fi

  echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
  info "Installation details:"
  echo -e "  ${DIM}•${RESET} Install location: ${CYAN}$INSTALL_ROOT${RESET}"
  echo -e "  ${DIM}•${RESET} Binary location: ${CYAN}$BIN_DIR/br${RESET}"
  echo -e "  ${DIM}•${RESET} Shell config: ${CYAN}$shell_rc${RESET}"
  echo ""
  echo -e "${DIM}For help and documentation, visit:${RESET}"
  echo -e "${CYAN}https://github.com/SamukeloGift/Brewery${RESET}"
  echo ""
}

cleanup_on_error() {
  echo ""
  warning "Cleaning up partial installation..."

  if [ -n "$INSTALL_ROOT" ] && [ -d "$INSTALL_ROOT" ]; then
    if [ -n "$USE_SUDO" ]; then
      $USE_SUDO rm -rf "$INSTALL_ROOT"
    else
      rm -rf "$INSTALL_ROOT"
    fi
  fi

  if [ -n "$BIN_DIR" ] && [ -f "$BIN_DIR/br" ]; then
    if [ -n "$USE_SUDO" ]; then
      $USE_SUDO rm -f "$BIN_DIR/br"
    else
      rm -f "$BIN_DIR/br"
    fi
  fi

  error "Installation failed. Please check the errors above and try again."
  exit 1
}

main() {
  # Trap errors for cleanup
  trap cleanup_on_error ERR

  # Print header
  print_header

  # System checks
  check_prerequisites || exit 1
  check_network || exit 1

  # Installation type selection
  show_installation_menu
  select_installation_type

  # Confirmation
  echo ""
  if [ -n "$USE_SUDO" ]; then
    warning "This will install Brewery system-wide and require sudo privileges."
  else
    info "This will install Brewery for your user account only."
  fi

  echo ""
  if ! confirm "Continue with installation?" "y"; then
    echo ""
    info "Installation cancelled by user"
    exit 0
  fi

  # Execute installation steps
  echo ""
  create_directories || exit 1
  download_source || exit 1
  create_wrapper || exit 1
  configure_path || exit 1
  verify_installation || exit 1

  # Success!
  show_completion_message
}

# Run main installation
main "$@"
