
<h1>Gobytego Remote Command Launcher
================================

A Utility for running remote commands

* * *

Overview
--------

The Gobytego Remote Command Launcher is a Python utility that simplifies the process of executing the same SSH command across multiple remote hosts concurrently. It handles secure authentication via SSH keys and manages the sudo password prompt, offering an interactive terminal window for each host to monitor output and respond to prompts in real-time.

1\. Setup & Installation
------------------------

### Prerequisites

*   \*\*Python 3:\*\* Required to run the launcher.
*   \*\*PyQt6:\*\* The graphical user interface library (install via `pip install PyQt6`).
*   \*\*Paramiko:\*\* The SSH library for secure connections (install via `pip install paramiko`).
*   \*\*SSH Key:\*\* A private SSH key (e.g., `~/.ssh/id_rsa`) configured for passwordless access to your remote hosts.

### Required Configuration Files

You must create these two text files in the same directory as the launcher script (`launcher.py`):

1\. gbg\_hosts.txt

<br>server-01.local
<br>db-staging.internal
<br>prod-web-03

(One hostname or IP address per line.)

2\. gbg\_commands.txt

<br>\\~/bin/upg\_1.01
<br>sudo dnf update -y
<br>sudo apt upgrade -y

(One full remote command per line, such as a script path or a package manager command.)

2\. How to Use the Launcher
---------------------------

### Configuration Fields

*   \*\*Remote User:\*\* The SSH username for all remote hosts.
*   \*\*SSH Key Path:\*\* The full path to your private key file (use the "Browse" button to set this).
*   \*\*Hosts File:\*\* Path to your `gbg_hosts.txt` file (use "Browse" to update this and reload the list below).
*   \*\*Commands File:\*\* Path to your `gbg_commands.txt` file (use "Browse" to update this and reload the command dropdown).
*   \*\*Remote Command:\*\* A dropdown list populated from your `gbg_commands.txt` file. Select the command you wish to execute.

### Host Selection

The center list displays all hosts loaded from the specified Hosts File. Simply check the boxes next to the hosts you want the command to run on.

### Execution

1.  \*\*Select Host(s) & Command:\*\* Check the desired hosts and select the command from the dropdown.
2.  \*\*Click 'Execute':\*\* Click the prominent button at the bottom.
3.  \*\*Sudo Password Prompt:\*\* A dialogue box will appear asking for the Sudo Password. This password will be automatically injected into the SSH session once the command is initiated.
4.  \*\*Interactive Terminals:\*\* For every selected host, a new Interactive Terminal Window will open.
5.  \*\*Monitor and Interact:\*\* You can monitor the real-time output in each window. If the remote command requires further user input (e.g., a confirmation prompt beyond the initial sudo injection), you can type directly into the terminal window to respond.

Key Features
------------

\*\*Interactive Sessions:\*\* Each host gets its own terminal for real-time output and manual input handling.

\*\*Settings Persistence:\*\* Host file paths, command file paths, user, key path, and host selections are automatically saved to `gbg_remote_settings.json`.

\*\*Dynamic Command Loading:\*\* Easily switch between complex remote commands without restarting the application by editing `gbg_commands.txt` and reloading the file.

\*\*Multi-Session Management:\*\* Allows concurrent execution and monitoring of commands across dozens of machines efficiently.

* * *
\*\*NOTE: When exiting a terminal window please use the button at the bottom "Close Session" not the upper right traditional close button (usually an "X") this will cause the program to think there is still an open session and if you try to execute another command that computer will be ignored.

© 2025 Gobytego Utilities. Built with Python, PyQt, and Paramiko.
