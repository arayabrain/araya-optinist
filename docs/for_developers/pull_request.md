## Pull Request
- GitHub Pull Request page
  - [https://github.com/arayabrain/araya-optinist/pulls](https://github.com/arayabrain/araya-optinist/pulls)

### Pre Commit
- run following command before your first commit
  ```
  pre-commit install
  ```
  - Once installed, it automatically checks your coding style on every commits.

### Branch Rules
- You can submit Pull Request by pushing new branch.
  - Make sure the base branch is `develop-subscription`, and PR is to `develop-subscription`.
  - You can't push to the `develop-subscription` branch directly, the branch is protected.
  - Make sure new branch name is in following format (`xxx` is the name of the feature or bug you are working on.).
    - `feature/xxx`
    - `fix/xxx`
