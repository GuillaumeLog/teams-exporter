# Teams Exporter

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white) [![Managed with uv](https://img.shields.io/badge/managed_with-uv-DE5FE9?logo=astral&logoColor=white)](https://docs.astral.sh/uv/) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE) ![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)

Export your direct Microsoft Teams conversations to a local, browsable HTML archive, or selectively clean up messages and files you sent. Teams Exporter is a local-first command-line application for Microsoft 365 work accounts: your configuration, token cache, and exports remain on your machine.

> [!IMPORTANT]
> **Tenant requirements:** Your Microsoft 365 tenant must allow device-code authentication, the Microsoft Graph Command Line Tools public client, and consent to the delegated permissions used by the selected command. Review the [Microsoft 365 tenant requirements](#31-microsoft-365-tenant-requirements) before running the project.

The project currently provides three commands:

- `export` builds an incremental HTML archive with conversations, participants, avatars, embedded images, and attachments;
- `purge` interactively soft-deletes your messages and eligible OneDrive files;
- `notifications` experimentally marks activity-feed entries as read and attempts to delete them through unsupported internal Teams endpoints.

> [!IMPORTANT]
> **Independent project:** This project is not affiliated with or supported by Microsoft.

> [!WARNING]
> **Data-loss risk:** Use this software at your own risk and back up any important data before running commands that modify remote content. The software is provided "as is" and, to the fullest extent permitted by law, its author and contributors are not liable for any data loss resulting from its use. See the [MIT License](LICENSE) for the full warranty and liability disclaimer.

> [!WARNING]
> `purge` modifies remote Microsoft 365 data. Review the command summary carefully before confirming any destructive operation.

> [!CAUTION]
> `notifications` uses undocumented, unsupported internal Teams endpoints that Microsoft may change or remove without notice.

## Table of contents

- [1. Quick start](#1-quick-start)
- [2. Prerequisites](#2-prerequisites)
  - [2.1 Linux](#21-linux)
  - [2.2 macOS](#22-macos)
  - [2.3 Windows](#23-windows)
- [3. Configuration](#3-configuration)
  - [3.1 Microsoft 365 tenant requirements](#31-microsoft-365-tenant-requirements)
  - [3.2 Local configuration](#32-local-configuration)
- [4. Commands](#4-commands)
  - [4.1 Export conversations](#41-export-conversations)
  - [4.2 Purge sent messages and files](#42-purge-sent-messages-and-files)
  - [4.3 Clean activity notifications](#43-clean-activity-notifications-experimental)
- [5. How it works and architecture](#5-how-it-works-and-architecture)
- [6. Testing and development](#6-testing-and-development)

## 1. Quick start

From a local clone of this repository on Linux or macOS:

```bash
cd teams-exporter
uv sync --locked
cp config.example.yaml config.yaml
# Edit config.yaml and set auth.tenant_id
uv run teams-exporter export
```

On Windows PowerShell:

```powershell
cd teams-exporter
uv sync --locked
Copy-Item config.example.yaml config.yaml
# Edit config.yaml and set auth.tenant_id
uv run teams-exporter export
```

Follow the displayed device-code prompt at <https://microsoft.com/devicelogin>. When the export finishes, open `output/index.html` in a browser.

## 2. Prerequisites

The only required tool is [`uv`](https://docs.astral.sh/uv/). It manages the virtual environment, dependencies, and Python runtime for the project. If no compatible Python 3.11+ installation is available, uv downloads one automatically.

The commands below come from the official [`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### 2.1 Linux

Install with the official standalone installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your shell if requested, then verify the installation:

```bash
uv --version
```

Distribution-specific packages and alternative methods are listed in the [`uv` installation documentation](https://docs.astral.sh/uv/getting-started/installation/).

### 2.2 macOS

Install with Homebrew:

```bash
brew install uv
```

Alternatively, use the official standalone installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then verify the installation:

```bash
uv --version
```

See the [`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/) for MacPorts and other supported methods.

### 2.3 Windows

Install from PowerShell with the official standalone installer:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Or install with WinGet:

```powershell
winget install --id=astral-sh.uv -e
```

Open a new terminal if requested, then verify the installation:

```powershell
uv --version
```

Additional methods are available in the [`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/).

## 3. Configuration

### 3.1 Microsoft 365 tenant requirements

Teams Exporter is intended for Microsoft 365 work or school accounts with access to Microsoft Teams. Before using it, confirm that the tenant meets these requirements:

- device-code authentication is permitted by the tenant's authentication and Conditional Access policies;
- the public **Microsoft Graph Command Line Tools** client is not blocked;
- the signed-in user, or a tenant administrator when required, can consent to the delegated permissions requested by the command;
- the account can access the Teams conversations being exported or cleaned;
- OneDrive is available when `purge.delete_onedrive_files` is enabled.

The commands request these delegated permissions only when needed:

| Command | Required delegated access |
| --- | --- |
| `export` | `Chat.Read`, `User.Read.All` |
| `purge` | `User.Read`, `Chat.ReadWrite`, plus `Files.ReadWrite.All` when OneDrive file deletion is enabled |
| `notifications` | `https://api.spaces.skype.com/.default` through a separate first-party Teams authentication flow |

Tenant consent policies determine whether the user can approve these permissions directly or an administrator must approve them. `User.Read.All` requires administrator consent, and tenant policy may require administrator approval for the other permissions as well. No Entra application registration or client secret is required.

The experimental `notifications` command also depends on undocumented Teams services. Even when Microsoft Graph access works, tenant security policies may block this separate authentication flow or the internal endpoints it uses.

### 3.2 Local configuration

Create a local configuration file from the tracked example:

```bash
cp config.example.yaml config.yaml
```

`config.yaml` is ignored by Git. At minimum, review the authentication section:

```yaml
auth:
  tenant_id: "organizations"
```

- `tenant_id` accepts a tenant GUID, a domain such as `contoso.onmicrosoft.com`, or the `organizations` alias.

Teams Exporter uses the public Microsoft Graph Command Line Tools client. You do not need to register an Entra application or create a client secret. Depending on your tenant's policies, an administrator may need to approve this client and its delegated permissions.

Teams Exporter stores its MSAL token caches in the standard user cache directory for the operating system:

| Platform | Directory |
| --- | --- |
| Linux | `$XDG_CACHE_HOME/teams-exporter`, or `~/.cache/teams-exporter` by default |
| macOS | `~/Library/Caches/teams-exporter` |
| Windows | `%LOCALAPPDATA%\teams-exporter\Cache` |

Graph and internal Teams authentication use separate `graph-token-cache.bin` and `teams-token-cache.bin` files. These files contain access and refresh tokens and are **not encrypted**. On POSIX systems, Teams Exporter restricts them to mode `0600`.

The remaining configuration sections control export filters and downloads, purge safety options, the experimental notifications feature, and logging. Every option is documented in [`config.example.yaml`](config.example.yaml).

## 4. Commands

### 4.1 Export conversations

#### 4.1.1 Description

`export` retrieves direct and group chats available to the signed-in user and produces a static local website. It supports incremental runs: unless `export.force` is enabled, existing conversation data and avatars are reused and only newer messages are fetched.

The command requests `Chat.Read` and `User.Read.All`. Configure `export.only` to limit the export to chat IDs or fragments of a topic or participant name.

#### 4.1.2 Run

```bash
uv run teams-exporter export
```

Use `--verbose` for detailed request and processing logs:

```bash
uv run teams-exporter export --verbose
```

#### 4.1.3 Result

The default output is:

```text
output/
├── index.html
├── app.js
├── style.css
├── avatars/
└── conversations/
    └── <conversation>/
        ├── index.html
        ├── conversation.json
        ├── media/
        └── messages/
```

Open `output/index.html` to browse the archive. The sidebar supports full-text search, sorting, filters for people, conversation type, and message presence, plus comfortable or compact display density. It displays results in batches of 100 to keep large lists responsive. Its header records the tenant, signed-in account, and export date so the archive remains identifiable when copied or stored long-term.

Each conversation page includes participant details, locally available media, and floating controls for jumping to the earliest loaded or latest message. Only the latest 200 messages are rendered initially; earlier messages are stored in local chunks and loaded on demand without requiring a web server. Existing exports using the previous flat directory layout are moved under `conversations/` automatically.

> [!IMPORTANT]
> Exports may contain confidential messages, contact details, images, and attachments. The default `output/` directory is ignored by Git, but custom output paths may not be. Never publish an export or attach one to a public issue.

### 4.2 Purge sent messages and files

#### 4.2.1 Description

`purge` scans the selected conversations for messages sent by the signed-in user. It uses an interactive, four-step safety flow:

1. select conversations from an unchecked list;
2. analyze messages and eligible files without deleting anything;
3. review per-conversation and total counts;
4. confirm the complete deletion plan.

Messages use Microsoft Graph `softDelete`, which is reversible. Files are only deleted when they belong to the signed-in user's OneDrive and are inside the **Microsoft Teams Chat Files** folder. SharePoint files and files elsewhere in OneDrive are skipped.

The command requests `Chat.ReadWrite`, plus `Files.ReadWrite.All` only when `purge.delete_onedrive_files` is enabled.

#### 4.2.2 Run

```bash
uv run teams-exporter purge
```

Useful safeguards in `config.yaml`:

```yaml
purge:
  only: []
  delete_onedrive_files: true
  hide_when_cleaned: true
```

#### 4.2.3 Result

After confirmation, the command reports successful and failed message or file operations for every selected conversation. If no message deletion fails and `purge.hide_when_cleaned` is enabled, the conversation is hidden from the current user's chat history. Hidden conversations reappear when new activity occurs.

Deleted OneDrive files are moved to the recycle bin. Soft-deleted messages remain recoverable by the service; the API does not provide permanent message deletion.

### 4.3 Clean activity notifications *(experimental)*

#### 4.3.1 Description

`notifications` marks unread Teams activity-feed entries as read and optionally attempts to delete them. Microsoft does not provide a public API for this operation, so the command uses undocumented internal Teams endpoints under `*.ng.msg.teams.microsoft.com` and a separate first-party Teams authentication flow.

This experimental feature may stop working without notice.

#### 4.3.2 Run

Configure whether deletion should be attempted:

```yaml
notifications:
  try_delete: true
```

Then run:

```bash
uv run teams-exporter notifications
```

The first run displays a second device-code prompt and creates a separate Teams token cache in the standard user cache directory described in [Configuration](#3-configuration).

#### 4.3.3 Result

The summary reports how many notifications were marked as read and how many deletion attempts succeeded or failed. Deletion may be unsupported even when marking entries as read succeeds. The regional messaging-service URL is discovered automatically during authentication.

## 5. How it works and architecture

Teams Exporter uses a small, layered architecture with explicit boundaries between authentication, remote APIs, business operations, and local rendering:

| Layer | Main modules | Responsibility |
| --- | --- | --- |
| CLI | `cli.py` | Parse commands, load configuration, select permission scopes, and manage exit codes. |
| Configuration | `config.py` | Load typed settings from `config.yaml` and resolve local paths. |
| Authentication | `auth.py` | Run the MSAL device code flow and persist the local token cache. |
| Microsoft Graph | `graph.py` | Perform authenticated requests, pagination, downloads, throttling, and retries. |
| Export | `export.py` | Fetch chats, normalize messages and participants, localize media, and persist JSON. |
| Purge | `purge.py` | Build a deletion plan, enforce ownership and folder safeguards, confirm, and execute it. |
| Notifications | `notifications.py` | Access the separate, unsupported internal Teams activity service. |
| Rendering | `render.py`, `templates/` | Sanitize message HTML and generate the static archive. |

### 5.1 Export data flow

1. The CLI loads configuration and requests read-only Graph scopes.
2. MSAL reuses a cached account or starts the device code flow.
3. `GraphClient` retrieves chats, messages, profiles, avatars, and permitted attachments.
4. `Exporter` merges new messages with any existing local conversation JSON.
5. `render.py` sanitizes remote HTML and renders the static site with Jinja2 templates.
6. The local JavaScript adds sidebar search and filtering, and progressively loads older message batches when requested.

The generated pages include a restrictive Content Security Policy. Message HTML is sanitized before it is marked safe for rendering, scripts can only be loaded from the export itself, and failed optional media downloads do not abort the rest of the export.

### 5.2 Purge safety boundaries

- only messages whose author ID matches the signed-in user are planned for deletion;
- no conversation is selected by default;
- no remote write occurs before the final confirmation;
- a file must resolve to the signed-in user's drive and the Microsoft Teams Chat Files folder before it can be deleted;
- a conversation is hidden only when all planned message deletions succeed.

Graph requests retry throttling and transient server failures with bounded exponential backoff. Notification requests use their own authentication token, endpoint discovery, retry loop, and cache file.

## 6. Testing and development

Install the project and development tools from the lockfile:

```bash
uv sync --locked --dev
```

Run the standard local checks:

```bash
# Unit tests
uv run pytest

# Static checks
uv run ruff check .

# Dependency vulnerability audit
uv run pip-audit --skip-editable

# Build wheel and source distribution
uv build
```

The test suite covers configuration loading, filename and path safety, HTML sanitization, token-cache permissions, OneDrive deletion boundaries, and purge failure behavior. GitHub Actions runs the suite on Python 3.11, 3.12, and 3.13, then builds the package and audits dependencies. Dependabot tracks both `uv` and GitHub Actions dependencies.

Project layout:

```text
src/teams_exporter/   Application source and HTML templates
tests/                Unit tests
.github/              CI, Dependabot, issue forms, and pull request template
config.example.yaml   Documented configuration reference
pyproject.toml        Package metadata and development-tool configuration
uv.lock               Reproducible dependency lockfile
```

Before submitting a change, read [CONTRIBUTING.md](CONTRIBUTING.md). Report security issues privately according to [SECURITY.md](SECURITY.md), and never include real Teams data, tenant identifiers, token caches, or exports in a public report.

Teams Exporter is available under the [MIT License](LICENSE). Microsoft, Microsoft Teams, Microsoft 365, OneDrive, and Microsoft Graph are trademarks of Microsoft Corporation. Their use here does not imply affiliation or endorsement.
