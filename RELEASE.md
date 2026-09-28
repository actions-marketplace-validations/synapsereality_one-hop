# Releasing one-hop

Nothing here has happened yet. Every step below is yours, from your own
accounts. The org name `synapsereality` is a placeholder. If you pick another
name, replace it everywhere with
`grep -rl --exclude-dir=.git synapsereality/ . | xargs sed -i 's#synapsereality/#NEWNAME/#g'`
(that leaves the synapsereality.io URLs alone), then commit.

## Once, for all three repos: the GitHub org

1. Create the org at https://github.com/account/organizations/new (Free plan),
   named `synapsereality`.
2. Org settings, Authentication security: tick "Require two-factor
   authentication". Marketplace publishing needs 2FA anyway.

## Once, for this repo

3. Create an empty public repo `synapsereality/one-hop`, with no README, licence
   or .gitignore, then push:

   ```bash
   cd ~/Documents/ben-is-a-dev/oss/one-hop
   git remote add origin git@github.com:synapsereality/one-hop.git
   git push -u origin main
   ```

4. Set the website field. It is the link people copy into tutorials, so it has
   to be our docs page and not GitHub:

   ```bash
   gh repo edit synapsereality/one-hop \
     --homepage https://synapsereality.io/open-source/one-hop/ \
     --description "Check a redirect map: every old URL must reach its new URL in one hop." \
     --add-topic redirects --add-topic seo --add-topic site-migration --add-topic github-action
   ```

5. Repo Settings, Environments, New environment, named `pypi`. Add yourself
   under "Required reviewers" if you want to click Approve before each upload.
   The release workflow waits for that click.

6. Publish the docs page first (`SITE-PAGE.md` in this folder, not in git) at
   https://synapsereality.io/open-source/one-hop/. The README and the package
   metadata already link to it.

## Once: PyPI trusted publisher (no token)

7. Log in at https://pypi.org, then Account settings, Publishing, "Add a new
   pending publisher", GitHub tab:

   | field | value |
   |---|---|
   | PyPI Project Name | `one-hop` |
   | Owner | `synapsereality` |
   | Repository name | `one-hop` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   A pending publisher does not reserve the name. If someone else registers
   `one-hop` before step 9, it is void, so do steps 7 to 9 on the same day. The
   name was free on 2026-09-29.

## Every release

8. Bump `version` in `pyproject.toml` and `CITATION.cff` (not needed for
   0.1.0), commit and push.
9. On GitHub: Releases, Draft a new release. Tag `v0.1.0` (create it on
   publish), target `main`, title `v0.1.0`.

   Under "Release Action", tick "Publish this Action to the GitHub
   Marketplace". The first time, the box is greyed out until you follow the
   link and accept the GitHub Marketplace Developer Agreement for the org.
   Pick "Continuous integration" as the primary category and "Utilities" as
   the second. The name in `action.yml` (one-hop redirect check) must not already be
   taken on the Marketplace. If it is, change `name:` in `action.yml` and
   commit before you release. Click Publish release.
10. The `release` workflow checks that the tag matches the version, runs the
    tests, builds, and uploads to PyPI. Check it under Actions, then:

    ```bash
    pip install one-hop==0.1.0
    one-hop --version
    ```

## Zenodo DOI (optional for this repo, before the first release)

Zenodo archives releases published after the switch is on. So flip it before
you publish the first release, or make a second release afterwards.

1. Log in at https://zenodo.org with GitHub. When GitHub asks, grant access to
   the `synapsereality` org.
2. Zenodo, account menu, GitHub: find `synapsereality/one-hop` and switch it on.
   Zenodo reads `.zenodo.json` for the title, creators, licence and links.
3. After the release, Zenodo shows two DOIs. Use the concept DOI, which always
   points to the latest version. Uncomment the `doi:` line in `CITATION.cff`,
   put it in, add a DOI line to the README, and commit.

## What is not automated, on purpose

No token for PyPI, npm or GitHub is stored in the repo or its secrets. The
workflows get a short-lived OIDC token from GitHub, valid for that one job, for
this repo, this workflow file and this environment.
