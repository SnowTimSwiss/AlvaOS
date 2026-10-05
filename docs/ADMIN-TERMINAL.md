# Admin terminal and assistant command proposals

This is the design for the terminal work in `ROADMAP.md` 1. It deliberately
does not add an endpoint or execute commands.

## Admin terminal

- Add an **Admin terminal** page for signed-in admins only. It is a real shell
  as the `alvaos` service account, with the same permissions as SSH access;
  never start a root shell and never use `sudo` as an escape hatch.
- Use a separate WebSocket service bound to localhost, reached through the
  authenticated AlvaOS backend. Authenticate the websocket upgrade with the
  normal session and origin checks; close it when the session expires or the
  admin signs out. Limit each session to one process, 30 minutes idle, and a
  small fixed number of concurrent sessions.
- Use a PTY for normal terminal behavior. Resize messages carry only bounded
  row and column counts. Cap input and output frames, terminate the process
  group on disconnect, and never persist terminal input or output in logs.
- Show a plain warning before opening: commands run as the AlvaOS service
  account and can damage NAS data. Offer copy and clear; do not retain history
  between sessions.
- The helper remains the only route to privileged operations. The terminal
  cannot invoke a shell as root and does not change helper policy.

## Assistant proposals

- Keep assistant diagnostics on the existing fixed, read-only endpoint list.
- Add a separate small catalog of named diagnostic and repair actions. Each
  entry has a fixed executable, fixed arguments or validated parameters,
  timeout, and a plain-language description. No free-form shell strings.
- The model may select a catalog entry and fill validated parameters. The UI
  displays the complete action and its effect; it runs only after an explicit
  click. Run it through the same backend and privilege policy used by the
  corresponding Settings or Storage control.
- Start with read-only diagnostics and a few reversible, well-understood
  repairs. Exclude file deletion, arbitrary package installation, and changes
  that expose the NAS to the network. Do not add a "run anything" mode.
- Record the selected action, who confirmed it, outcome and a short sanitized
  result in the existing audit/notification path; never record secrets.

## Release checks

Before release, verify administrator and non-administrator access, session
expiry, websocket origin rejection, disconnect cleanup, output limits, and
that neither the terminal nor assistant action catalog can bypass
`priv_policy.py`. A hardware check should include a slow NAS and a phone-sized
browser view.
