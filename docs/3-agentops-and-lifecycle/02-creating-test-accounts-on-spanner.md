# Creating Test Gaia Email Accounts on Spanner

This guide explains how to create and manage test Gaia email accounts (`@gmail.com`) for testing Esmeralda agents, microservices, and SecOps workflows using the Google internal `setup_test_account_on_spanner` Blaze tool.

---

## 📋 Prerequisites

Before running the creation command, ensure you have:

1. **Active `gcert` Credentials**:
   ```bash
   gcert
   ```
2. **Active Citc / google3 Workspace**:
   Blaze commands must be executed inside a google3 Citc workspace directory:
   ```bash
   cd /google/src/cloud/$USER/<workspace_name>/google3
   ```
3. **MDB Group Membership**:
   The creation service requires an MDB group you belong to. Query your active MDB groups:
   ```bash
   ganpati2 ancestors --id $USER.prod
   ```

---

## 🚀 Creating a New Test Account

To create a new test account, use the `//caribou/testing/backend/tool:setup_test_account_on_spanner` Blaze target.

### Standard Execution Command

```bash
cd /google/src/cloud/$USER/<workspace_name>/google3

blaze run //caribou/testing/backend/tool:setup_test_account_on_spanner -- \
  --mode=create \
  --user_email=esmeraldasecops@gmail.com \
  --password='(Julio)@01' \
  --owner_mdb=vector-users \
  --noprepopulate_mailbox
```

---

## ⚠️ Mandatory Validation Rules

When specifying parameters for `setup_test_account_on_spanner`, enforce the following rules:

| Rule | Requirement | Example | Reason |
| :--- | :--- | :--- | :--- |
| **Email Domain** | Must end in `@gmail.com` or `@id.gle`. | `esmeraldasecops@gmail.com` | `@google.com` addresses are rejected by the Test Account Service. |
| **Username Format** | Strictly **alphanumeric** characters (lowercase letters and digits). **No hyphens (`-`) or underscores (`_`)**. | `esmeraldasecops@gmail.com` | Hyphens and underscores trigger `CheckCanonicalEmail ILLEGAL_CHARS` errors. |
| **Password Quoting** | Wrap the password argument in **single quotes `'`**. | `--password='(Julio)@01'` | Special characters like `(` or `)` cause Bash shell syntax errors. |
| **Owner MDB Group** | Specify an MDB group without the `mdb/` prefix that your LDAP user belongs to. | `--owner_mdb=vector-users` | The service checks ACL membership against your user ID. |

---

## 🛠️ Command Flag Reference

| Flag | Required | Description |
| :--- | :--- | :--- |
| `--mode` | **Yes** | Operation mode: `create`, `modify`, `modify_hms`, or `post_create`. |
| `--user_email` | **Yes** | Alphanumeric test account email (ending in `@gmail.com`). |
| `--password` | **Yes** | Quoted password string for the test account. |
| `--owner_mdb` | **Yes** (in `create` mode) | MDB group owning the test account (without `mdb/` prefix). |
| `--noprepopulate_mailbox` | Optional | Disables automatic mailbox transactional pre-population. |

---

## 🔍 Troubleshooting Common Errors

### 1. `bash: syntax error near unexpected token '('`
- **Cause**: Password contains unquoted parentheses or shell special characters.
- **Fix**: Enclose the password in single quotes: `--password='(Julio)@01'`.

### 2. `Must specify either 'base.current_directory' or 'base.workspace_id.workspace_name'`
- **Cause**: Command executed outside a valid google3 / Citc directory (e.g. inside local git workspace).
- **Fix**: Change directory to your Citc google3 workspace: `cd /google/src/cloud/$USER/<workspace>/google3`.

### 3. `generic::INVALID_ARGUMENT: only @gmail.com and @id.gle accounts can be created`
- **Cause**: Provided `@google.com` or custom domain.
- **Fix**: Change email domain to `@gmail.com`.

### 4. `generic::PERMISSION_DENIED: User <user> is not a member of <group>`
- **Cause**: Specified an MDB group you do not belong to.
- **Fix**: Run `ganpati2 ancestors --id $USER.prod` and choose an MDB group you are a member of.

### 5. `CheckCanonicalEmail error code = ILLEGAL_CHARS`
- **Cause**: Email prefix contains hyphens (`-`) or underscores (`_`).
- **Fix**: Remove all special characters from the username (e.g. use `esmeraldasecops` instead of `esmeralda-secops`).
