# Welcome to Xlator

## How can I create a repo like that uses the Xlator plugin?

The `download.sh` script downloads the Xlator repo template (and sets up a new git repository if an argument is provided).

In a terminal, use the `download.sh` script as follows:

```bash
curl -s https://raw.githubusercontent.com/navapbc/lockpicks-xlator-plugin/main/download.sh | bash -s -- [new_repo_path] [domains_subfolder_name]
```

If the arguments [new_repo_path] and [domains_subfolder_name]are provided, `create_git_repo.sh` is run to create a new repository at `new_repo_path` with a subfolder named `domains_subfolder_name` (defaults to 'domains').

If the arguments are not provided, the template will be downloaded and left in a folder named `xlator-repo-creator` for manual execution of `create_git_repo.sh`.
