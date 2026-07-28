# Discord GitHub Todos and Projects

The Discord agent uses GitHub Issues as its todo system. `508-dev/todos` is the
built-in default repository, so it does not need to be repeated in deployment
configuration or in ordinary Discord requests.

## Access model

- A Discord user must hold at least the `Member` role to use GitHub todo tools.
- Members can read, create, update, close/reopen, and comment on issues in
  `508-dev/todos`.
- `Steering Committee`, `Admin`, and `Owner` can read and write every repository
  selected for the GitHub App installation. They can also read and update
  organization-owned GitHub Projects.
- All writes require the existing Discord confirmation step. The bot does not
  need a linked GitHub user; GitHub records the action as the installed app.

`GITHUB_MEMBER_EXTRA_REPOS` is available only when all Members should also use
additional repositories. Keep it empty for the default todo-only setup.

## GitHub App setup

Create one GitHub App owned by `508-dev`, then install it on **Only select
repositories**. Add repositories to that installation when Steering Committee
access should include them.

Request the minimum permissions:

- Repository **Issues: Read and write**
- Repository **Metadata: Read-only** (implicit)
- Organization **Projects: Read and write**

Do not grant Contents, Actions, administration, member, or webhook permissions
for this feature. In **Dashboard → Configuration → Operations**, set the
GitHub App **Client ID**, installation ID, and private key. The private key is
encrypted in the runtime configuration database, so `CONFIG_SECRET_KEY` must
remain configured in the deployment. Environment variables are optional and
override dashboard values when set.

The service creates short-lived installation tokens. Issue tokens are narrowed
to the target repository and requested permission; Project tokens request only
the organization Projects permission. GitHub installation tokens expire after
one hour and the backend refreshes them before expiry.

## Configuration

Only the App credentials are required. Prefer configuring them through the
dashboard; the equivalent environment variables are:

```dotenv
GITHUB_APP_CLIENT_ID=Iv1.0123456789abcdef
GITHUB_APP_INSTALLATION_ID=...
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n..."
```

`GITHUB_APP_ID` is accepted as a temporary compatibility alias, but GitHub
recommends the Client ID as the JWT issuer. Dashboard-managed values take
effect across services within the short runtime-config cache window (about five
seconds); a non-empty environment value locks and overrides its dashboard
setting.

These secure defaults are already present in code:

```dotenv
GITHUB_DEFAULT_REPO=508-dev/todos
GITHUB_ORGANIZATION=508-dev
GITHUB_STEERING_ALL_INSTALLED_REPOS=true
```

Set `GITHUB_STEERING_ALL_INSTALLED_REPOS=false` and populate
`GITHUB_STEERING_EXTRA_REPOS` only if Steering Committee access should be
restricted below the App installation's selected repositories. `GITHUB_API_TOKEN`
and `GITHUB_ALLOWED_REPOS` remain a temporary compatibility path for a gradual
migration; remove them once the App is deployed.

## Discord examples

- `show open todos`
- `show todo #42`
- `create todo: Follow up with the vendor`
- `complete todo #42`
- `comment on todo #42: Waiting on the invoice`
- `list GitHub Projects`
- `show GitHub project #3 items`
- `add issue #42 to GitHub project #3`
- `set GitHub project #3 item #99 field #5 to Done`

For field updates, first ask the bot to list a project's fields and items, then
provide the project item ID, field ID, and value in the confirmation request.
