# Create a New Domain

Set up the standard folder scaffold for a new domain so it's ready for policy document ingestion and rule extraction.

## Input

```
/new-domain <domain>
```

If `<domain>` is not provided, prompt: "What should the domain be named? (e.g., `snap`, `ak_doh`)"

## Pre-flight

1. **Domain name provided?** — If not, prompt for it. Then continue.

2. **Confirm domain path:**
   Source `xlator.conf` in the project root to get `DOMAINS_DIR`. Print:
   ```
   This will create: $DOMAINS_DIR/<domain>/
   Continue? [y/n]
   ```
   If user answers n → stop.

## Process

### Step 1: Create folder structure

```bash
xlator new-domain <domain>
```

### Step 2: Print next steps

```
Domain '<domain>' is ready at $DOMAINS_DIR/<domain>/

Next steps:
  1. Add .md policy documents to `$DOMAINS_DIR/<domain>/input/policy_docs/`
  2. Run `/xl:index-inputs <domain>` to build a document index
  3. Run /xl:refine-guidance <domain> to set extraction goals and ruleset guidance
  4. Run /xl:extract-ruleset <domain> to extract the CIVIL ruleset
```

## Common Mistakes to Avoid

- Domain names must be valid directory names: lowercase letters, digits, underscores, no spaces (e.g., `snap`, `ak_doh`, `ca_calworks`)
