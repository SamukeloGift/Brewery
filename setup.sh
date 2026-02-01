#!/bin/bash

GITHUB_RAW_URL="https://raw.githubusercontent.com/SamukeloGift/Brewery/main/main.py"
USER_ROOT="$HOME/.br"

ARCH=$(uname -m)
if [ "$ARCH" == "arm64" ]; then
  GLOBAL_ROOT="/opt/br"
else
  GLOBAL_ROOT="/usr/local/br"
fi

SHELL_RC="$HOME/.zshrc"

echo "Brewery (br) Installation Manager"
echo "--------------------------------"

echo "Choose installation type:"
echo "1) User-level (Files in $HOME/.br)"
echo "2) System-level (Files in $GLOBAL_ROOT - Requires sudo ONCE for setup)"
printf "Enter choice [1-2]: "
read choice < /dev/tty

if [ "$choice" == "2" ]; then
  INSTALL_ROOT="$GLOBAL_ROOT"
  BIN_DIR="/usr/local/bin"
  echo "Setting up system-level directories..."
  sudo mkdir -p "$INSTALL_ROOT/Cellar"
  sudo mkdir -p "$INSTALL_ROOT/bin"
  sudo chown -R $(whoami):admin "$INSTALL_ROOT"
  USE_SUDO_FOR_WRAPPER="sudo"
else
  INSTALL_ROOT="$USER_ROOT"
  BIN_DIR="$USER_ROOT/bin"
  mkdir -p "$INSTALL_ROOT/Cellar"
  mkdir -p "$BIN_DIR"
  USE_SUDO_FOR_WRAPPER=""
fi

echo "Fetching source from GitHub..."
curl -fsSL "$GITHUB_RAW_URL" -o "$INSTALL_ROOT/main.py"

if [ $? -ne 0 ]; then
    echo "Error: Failed to download main.py"
    exit 1
fi

WRAPPER_PATH="$BIN_DIR/br"
echo "Creating command wrapper at $WRAPPER_PATH..."

cat <<EOF | $USE_SUDO_FOR_WRAPPER tee "$WRAPPER_PATH" >/dev/null
#!/bin/bash
export BREWERY_ROOT="$INSTALL_ROOT"
exec uv run --no-project "$INSTALL_ROOT/main.py" "\$@"
EOF

$USE_SUDO_FOR_WRAPPER chmod +x "$WRAPPER_PATH"

if [ "$choice" == "2" ]; then
  echo "------------------------------------------------"
  echo "Setup complete. Command 'br' is now available system-wide."
else
  if ! grep -q "$BIN_DIR" "$SHELL_RC"; then
    echo "" >> "$SHELL_RC"
    echo "# Brewery (br) Path Configuration" >> "$SHELL_RC"
    echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$SHELL_RC"
  fi
  echo "------------------------------------------------"
  echo "Setup complete. Run 'source ~/.zshrc' to start."
ficat <<EOF | $USE_SUDO_FOR_WRAPPER tee "$WRAPPER_PATH" >/dev/null
#!/bin/bash
export BREWERY_ROOT="$INSTALL_ROOT"
exec uv run --no-project "$INSTALL_ROOT/main.py" "\$@"
EOF

$USE_SUDO_FOR_WRAPPER chmod +x "$WRAPPER_PATH"

if [ "$choice" == "1" ]; then
  ABS_BIN_DIR="$HOME/.br/bin"
  if [[ ":$PATH:" != *":$ABS_BIN_DIR:"* ]]; then
    if ! grep -q "$ABS_BIN_DIR" "$SHELL_RC"; then
      echo "" >>"$SHELL_RC"
      echo "# Brewery (br) Path Configuration" >>"$SHELL_RC"
      echo "export PATH=\"\$HOME/.br/bin:\$PATH\"" >>"$SHELL_RC"
      echo "Path configuration added to $SHELL_RC"
    fi
  fi
  echo "------------------------------------------------"
  echo "Setup complete."
  echo "CRITICAL: Run the following command to start using 'br' now:"
  echo "source ~/.zshrc"
else
  echo "------------------------------------------------"
  echo "Setup complete. Command 'br' is now available system-wide."
fi
