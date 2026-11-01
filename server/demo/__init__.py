"""Demo mode: `./run.sh demo` runs learnMe on a scripted backend, so it can be tried without any key.

Everything around the model is the real thing: the manager's tools, automatic supervision, the
alert intake and the watcher, memory tracking and the rule diff. Only the agents' words and their
tool output come from a script (backend.py), and a director (story.py) plays the user: it asks for
three tasks, sends an alert, corrects the manager once and drops a piece of user feedback.

The demo runs in data/demo (wiped on every start) with made-up projects, on its own port (8777).
Nothing in it is real, and no model is called.
"""
