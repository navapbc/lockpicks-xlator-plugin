# Set Up Xlator for This Project

Configure the xlator plugin for use in this project. Run this once after installing the plugin, and again after upgrading it.

## Input

```
/setup <domains_dir>
```

If `<domains_dir>` is provided, skip Step 1 and use `<domains_dir>` as the user's `${DOMAINS_DIR}` answer for Step 1.

## Pre-flight

1. **Determine plugin directory:**
   The plugin root is available as `${CLAUDE_PLUGIN_ROOT}`. Verify:
   ```bash
   ls "${CLAUDE_PLUGIN_ROOT}/xlator.py"
   ```
   If not found → print: "Cannot locate plugin root. Please reinstall the xlator plugin." and stop.

## Process

### Step 1: Ask for Domains Directory

If `xlator.conf` exists in the PROJECT_ROOT, read the `DOMAINS_DIR` value from the file.
Otherwise set `DOMAINS_DIR` to the default `domains/`.

Ask the user:
```
Where should ruleset domains live in your project? (default: $DOMAINS_DIR)
```

Use the user's answer as `${DOMAINS_DIR}`.

### Step 2: Write `xlator.conf`

Create `xlator.conf` in the PROJECT_ROOT:

```bash
cat > xlator.conf << EOF
export DOMAINS_DIR=${DOMAINS_DIR}
EOF
```

### Step 3: `xlator setup`: Create the Python virtual environment, Install OCaml/Catala Toolchain

Run the standard xlator setup (handles mise, uv, python venv, opam, catala):

```bash
"${CLAUDE_PLUGIN_ROOT}/xlator" setup
```

Wait for completion — this may take several minutes on first run (opam initializes OCaml).

### Step 4: Confirm

Print:
```
✓ xlator.conf written
✓ Tools installed

Setup complete! Run /xl:new-domain <name> to create your first domain.
```
