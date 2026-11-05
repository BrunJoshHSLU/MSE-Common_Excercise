# Annex 96 Common Exercise – HSLU Workspace

Shared repository for us working on the
**IEA EBC Annex 96 Common Exercise 1**.

The simulation environment, data and evaluation scripts are copied from the original exercise repository:
👉 https://github.com/kkaspar10/annex96_common_exercise_1

Each participant develops their own controller(s) in a personal folder, so we all use the
same environment and evaluation and don't get in each other's way.

---

## Repository structure

```
.
├── common/                  # Copy of the original Annex repo (environment, data, evaluation)
│                            # ⚠️ Do not edit – see "Shared code" below
├── participants/
│   ├── _template/           # Starting point for a new controller
│   ├── <firstname_lastname>/
│   │   ├── controller.py    # Your controller(s)
│   │   ├── run.py           # Script / notebook to run and evaluate your controller
│   │   └── README.md        # Short description of your approach and results
│   └── ...
├── requirements.txt         # Python dependencies (same as the original repo)
└── README.md
```

---

## Getting started

1. **Clone the repo**
   ```bash
   git clone <URL OF THIS REPO>
   cd <REPO NAME>
   ```

2. **Set up the Python environment**
   ```bash
   python -m venv .venv
   # Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Create your own folder**
   Copy `participants/_template/` to `participants/<firstname_lastname>/` and start working there.

4. **Run and evaluate** your controller using the environment and evaluation scripts in `common/`,
   following the instructions in the original exercise repository.

---

## Working together

- **Only edit files in your own folder.** That way there are no merge conflicts.
- **Shared code (`common/`) stays unchanged** so everyone's results are comparable.
  If something there needs fixing, open an Issue or a Pull Request and discuss it with the group first.
- **Pull before you push:** `git pull` before starting work, then commit and push your changes.
- Write a short `README.md` in your folder explaining your controller and your results.

---

## Updating from the original repo

If the original exercise repository gets updated, one person updates `common/` and tells the group.

---

## Participants

| Name | Folder | Controller approach |
|------|--------|---------------------|
|      | `participants/...` |              |

---

## Credits

Environment, data and evaluation by the IEA EBC Annex 96 Common Exercise team –
see the [original repository](https://github.com/kkaspar10/annex96_common_exercise_1)
and its license.
