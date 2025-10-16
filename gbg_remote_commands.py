import sys
import os
import paramiko 
import re 
import time
import json 

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QListWidget, QListWidgetItem, QLineEdit, 
    QLabel, QCheckBox, QInputDialog, QMessageBox, QFileDialog, 
    QDialog, QPlainTextEdit, QComboBox 
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QDir
from PyQt6.QtGui import QColor, QPalette, QFont

import qdarkstyle

# --- Configuration and Data Persistence ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# UPDATED FILE NAMES
SETTINGS_FILE = os.path.join(SCRIPT_DIR, 'gbg_remote_settings.json')

DEFAULT_SETTINGS = {
    'host_file_path': os.path.join(SCRIPT_DIR, 'gbg_hosts.txt'),
    'command_file_path': os.path.join(SCRIPT_DIR, 'gbg_commands.txt'), 
    'remote_user': os.environ.get('USER', 'adam'),
    'ssh_key_path': os.path.expanduser('~/.ssh/id_rsa'),
    'selected_command': '~/bin/upg_1.01' # Default command, now selectable
}

def load_settings():
    """Loads settings from the JSON file or returns defaults."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                return {**DEFAULT_SETTINGS, **settings}
        except json.JSONDecodeError:
            print(f"Warning: Corrupted settings file {SETTINGS_FILE}. Using defaults.")
    return DEFAULT_SETTINGS

def save_settings(settings):
    """Saves current settings to the JSON file."""
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)


# --- Host and Command File Loading ---

def load_file_lines(file_path, default_line=''):
    """Loads non-commented, non-empty lines from a file."""
    lines = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        lines.append(line)
        except Exception as e:
            QMessageBox.critical(None, "File Read Error", f"Error reading file {file_path}:\n{e}")
    
    # If file is empty or missing and a default is provided, ensure it's there.
    if not lines and default_line:
        lines.append(default_line)
    
    return lines


# --- Static Configuration ---
ANSI_ESCAPE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

# ----------------------------------------------------------------------
## Helper Widgets 
# ----------------------------------------------------------------------

class HostItemWidget(QWidget):
    """A custom widget to hold the checkbox and the hostname."""
    def __init__(self, name, is_checked=False, parent=None):
        super().__init__(parent)
        self.hostname = name 
        self.checkbox = QCheckBox(name)
        self.checkbox.setChecked(is_checked)
        self.setToolTip(f"Host: {name}")

        layout = QHBoxLayout(self)
        layout.addWidget(self.checkbox)
        layout.setContentsMargins(5, 0, 5, 0)

class TerminalWindow(QDialog):
    
    user_input_ready = pyqtSignal(str)

    def __init__(self, parent=None, host_name="", command=""): 
        super().__init__(parent)
        self.setWindowTitle(f"Connected to: {host_name}")
        self.host_name = host_name
        self.command = command 
        self.setMinimumSize(700, 500)
        
        self.input_queue = [] 
        self.worker = None 
        
        layout = QVBoxLayout(self)
        
        self.terminal_output = QPlainTextEdit()
        self.terminal_output.setReadOnly(True) 
        
        font = QFont("Monospace")
        font.setPointSize(10)
        self.terminal_output.document().setDefaultFont(font)
        
        palette = self.terminal_output.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(20, 20, 20)) 
        palette.setColor(QPalette.ColorRole.Text, QColor(240, 240, 240)) 
        self.terminal_output.setPalette(palette)
        
        self.terminal_output.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.terminal_output.setFocus()

        layout.addWidget(self.terminal_output)
        
        close_button = QPushButton("Close Session")
        close_button.clicked.connect(self.close_session_and_window)
        layout.addWidget(close_button)

    def close_session_and_window(self):
        """Cleanly terminates the worker thread and closes the window. Sets flag to suppress error."""
        if self.worker and self.worker.isRunning():
            try:
                # FIX: Set the flag to tell the worker this is a user-initiated close
                self.worker.user_closing = True 
                
                self.worker.log_output.emit("\n[TERMINATING] Session closed by user.\n")
                if self.worker.client and self.worker.client.get_transport():
                    self.worker.client.get_transport().close() 
            except Exception:
                pass 
            finally:
                self.worker.quit()
                self.worker.wait(500) 

        self.accept()

    def append_log(self, text):
        """Appends text (hopefully preserving TTY formatting) and scrolls."""
        clean_text = ANSI_ESCAPE.sub('', text)
        self.terminal_output.insertPlainText(clean_text)
        self.terminal_output.ensureCursorVisible()

    def keyPressEvent(self, event):
        """Captures keyboard input and sends it to the worker thread via the queue."""
        
        if self.isVisible():
            key = event.key()
            text = event.text()

            if key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
                self.input_queue.append('\n')
                self.terminal_output.insertPlainText('\n') 
            
            elif text:
                self.input_queue.append(text)
                self.terminal_output.insertPlainText(text)
            
            elif key == Qt.Key.Key_Backspace:
                self.input_queue.append('\x7F') 
                
                cursor = self.terminal_output.textCursor()
                if cursor.hasSelection():
                    cursor.removeSelectedText()
                elif cursor.position() > 0:
                    cursor.deletePreviousChar()


# ----------------------------------------------------------------------
## Worker Thread 
# ----------------------------------------------------------------------
class InteractiveUpgradeWorker(QThread):
    log_output = pyqtSignal(str)
    session_error = pyqtSignal(str, str) 
    session_complete = pyqtSignal(str) 
    
    def __init__(self, host, user, ssh_key_path, sudo_password, remote_command, input_queue):
        super().__init__()
        self.host = host
        self.user = user
        self.ssh_key_path = ssh_key_path 
        self.sudo_password = sudo_password
        self.remote_command = remote_command 
        self.input_queue = input_queue
        self.client = None 
        # FIX: Flag to indicate manual closure vs. remote failure
        self.user_closing = False

    def run(self):
        self.log_output.emit(f"--- Establishing session on: {self.host} as user: {self.user} using key: {self.ssh_key_path} ---\n")
        self.log_output.emit(f"--- Executing command: {self.remote_command} ---\n")
        
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            key = paramiko.RSAKey.from_private_key_file(self.ssh_key_path)
            self.client.connect(hostname=self.host, username=self.user, pkey=key)

            channel = self.client.invoke_shell()
            channel.settimeout(0.0)
            
            # Use the dynamic command
            command_start = f'sudo -S {self.remote_command}\n'
            channel.send(command_start)

            password_sent = False
            
            while not channel.exit_status_ready():
                if channel.recv_ready():
                    output_bytes = channel.recv(4096)
                    output_str = output_bytes.decode('utf-8', errors='ignore')
                    
                    if not password_sent and ("password for" in output_str.lower() or "sudo" in output_str.lower()):
                        channel.send(self.sudo_password + '\n')
                        self.log_output.emit("Password accepted. Running command...\n")
                        password_sent = True
                    else:
                        self.log_output.emit(output_str)

                if self.input_queue:
                    user_input = self.input_queue.pop(0)
                    if user_input:
                        channel.send(user_input)

                self.msleep(20)

            exit_code = -1
            if channel.exit_status_ready():
                exit_code = channel.recv_exit_status()
            
            final_message = f"\n--- Session finished on {self.host}. Exit code: {exit_code} ---\n"
            self.log_output.emit(final_message)
            
            if exit_code != 0:
                # FIX: Check the flag to suppress error on manual close
                if not self.user_closing:
                    self.session_error.emit(self.host, f"Command failed with exit code {exit_code}")
                else:
                    print(f"Session closed by user on {self.host}. Suppressing exit code {exit_code} error.")
            else:
                self.session_complete.emit(self.host)

        except paramiko.AuthenticationException:
            self.session_error.emit(self.host, "Authentication failed (Check key or user).")
            self.log_output.emit("\n[FATAL] Authentication Failed.\n")
        except paramiko.SSHException as e:
            # FIX: Check flag for SSH errors caused by user closing the connection
            if not self.user_closing:
                 self.session_error.emit(self.host, f"SSH error: {e}")
                 self.log_output.emit(f"\n[FATAL] SSH Error: {e}\n")
            else:
                 print(f"Expected SSH exception on user close for {self.host}. Error: {e}")
        except Exception as e:
            self.session_error.emit(self.host, f"General error: {e}")
            self.log_output.emit(f"\n[FATAL] General Error: {e}\n")
        finally:
            if self.client:
                self.client.close()


# ----------------------------------------------------------------------
## Main Application Window 
# ----------------------------------------------------------------------
class GobytegoRemoteCommandLauncher(QWidget): # RENAMED CLASS
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        
        # FIX: Initialize self.host_list_data before loading hosts 
        self.host_list_data = [] 
        self.commands = [] 
        
        self.host_list_data = self.load_hosts(self.settings['host_file_path'])
        self.commands = load_file_lines(self.settings['command_file_path'], DEFAULT_SETTINGS['selected_command']) # Load commands
        self.active_sessions = [] 
        self.init_ui()

    def load_hosts(self, file_path):
        """Loads host data from the specified text file."""
        hosts_data = []
        hosts = load_file_lines(file_path)
        
        for host in hosts:
            # Preserve original 'checked' state if host is still in the list
            existing_data = next((item for item in self.host_list_data if item['name'] == host), None)
            is_checked = existing_data['checked'] if existing_data else True
            hosts_data.append({'name': host, 'checked': is_checked})
            
        return hosts_data

    def init_ui(self):
        # UPDATED MAIN WINDOW TITLE
        self.setWindowTitle("Gobytego Remote Command Launcher")
        self.setMinimumSize(450, 530) 
        
        main_layout = QVBoxLayout(self)

        # --- Settings/Config Section ---
        config_group = QVBoxLayout()
        config_group.setSpacing(5)
        
        # 1. Remote User Line
        user_layout = QHBoxLayout()
        user_layout.addWidget(QLabel("Remote User:"))
        self.user_input = QLineEdit(self.settings['remote_user'])
        self.user_input.setToolTip("The username used for SSH login on remote servers.")
        user_layout.addWidget(self.user_input)
        config_group.addLayout(user_layout)

        # 2. SSH Key Path Selector
        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel("SSH Key Path:"))
        self.key_input = QLineEdit(self.settings['ssh_key_path'])
        self.key_input.setReadOnly(True)
        self.key_input.setToolTip("Path to the private SSH key file (e.g., ~/.ssh/id_rsa).")
        key_layout.addWidget(self.key_input)
        
        self.key_button = QPushButton("Browse")
        self.key_button.setMaximumWidth(80)
        self.key_button.clicked.connect(self.select_ssh_key_file)
        key_layout.addWidget(self.key_button)
        
        config_group.addLayout(key_layout)

        # 3. Host File Selector
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("Hosts File:"))
        self.file_input = QLineEdit(self.settings['host_file_path'])
        self.file_input.setReadOnly(True)
        self.file_input.setToolTip("Path to the file containing hostnames (one per line).")
        file_layout.addWidget(self.file_input)
        
        self.file_button = QPushButton("Browse")
        self.file_button.setMaximumWidth(80)
        self.file_button.clicked.connect(self.select_host_file)
        file_layout.addWidget(self.file_button)
        
        config_group.addLayout(file_layout)
        
        # 4. Command File Selector
        cmd_file_layout = QHBoxLayout()
        cmd_file_layout.addWidget(QLabel("Commands File:"))
        self.cmd_file_input = QLineEdit(self.settings['command_file_path'])
        self.cmd_file_input.setReadOnly(True)
        self.cmd_file_input.setToolTip("Path to the file containing remote commands (one per line).")
        cmd_file_layout.addWidget(self.cmd_file_input)
        
        self.cmd_file_button = QPushButton("Browse")
        self.cmd_file_button.setMaximumWidth(80)
        self.cmd_file_button.clicked.connect(self.select_command_file)
        cmd_file_layout.addWidget(self.cmd_file_button)
        
        config_group.addLayout(cmd_file_layout)

        # 5. Remote Command Dropdown
        command_layout = QHBoxLayout()
        command_layout.addWidget(QLabel("Remote Command:"))
        self.command_dropdown = QComboBox()
        self.command_dropdown.setToolTip("Select the command to execute remotely.")
        self.populate_command_dropdown()
        command_layout.addWidget(self.command_dropdown)
        config_group.addLayout(command_layout)
        
        main_layout.addLayout(config_group)
        main_layout.addSpacing(10)

        # --- List Widget ---
        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(200)
        main_layout.addWidget(self.list_widget)
        self.populate_list()
        main_layout.addSpacing(10)
        
        # --- Footer ---
        footer_label = QLabel(f"Settings saved to: {os.path.basename(SETTINGS_FILE)}")
        footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer_label.setStyleSheet("font-style: italic; color: #888;")
        main_layout.addWidget(footer_label)
        
        main_layout.addSpacing(10)
        
        # --- Execute Button ---
        self.execute_button = QPushButton("Execute Command on Selected Hosts")
        self.execute_button.clicked.connect(self.prompt_and_execute)
        self.execute_button.setStyleSheet("font-size: 16px; padding: 10px; font-weight: bold;")
        main_layout.addWidget(self.execute_button)
        
    def populate_command_dropdown(self):
        """Populates the command dropdown based on loaded commands and settings."""
        self.command_dropdown.clear()
        
        # Load commands fresh (using the current path stored in self.settings)
        self.commands = load_file_lines(self.settings['command_file_path'], DEFAULT_SETTINGS['selected_command'])
        
        if not self.commands:
            self.command_dropdown.addItem("No commands found (check gbg_commands.txt)")
            self.command_dropdown.setEnabled(False)
            return

        self.command_dropdown.setEnabled(True)
        self.command_dropdown.addItems(self.commands)
        
        # Restore the previously selected command
        try:
            index = self.commands.index(self.settings.get('selected_command', DEFAULT_SETTINGS['selected_command']))
            self.command_dropdown.setCurrentIndex(index)
        except ValueError:
            # If the old command is no longer in the file, select the first item
            self.command_dropdown.setCurrentIndex(0)


    def select_ssh_key_file(self):
        """Opens a file dialog to select the SSH key file."""
        current_path = self.key_input.text()
        default_dir = os.path.dirname(current_path) if os.path.isdir(os.path.dirname(current_path)) else os.path.expanduser('~/.ssh')
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Private SSH Key File", default_dir, "All Files (*)"
        )
        
        if file_path:
            self.key_input.setText(file_path)
            self.settings['ssh_key_path'] = file_path
            save_settings(self.settings)

    def select_host_file(self):
        """Opens a file dialog to select the hosts file."""
        current_path = self.file_input.text()
        default_dir = os.path.dirname(current_path) if os.path.isdir(os.path.dirname(current_path)) else SCRIPT_DIR
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Hosts File", default_dir, "Text Files (*.txt);;All Files (*)"
        )
        
        if file_path:
            self.file_input.setText(file_path)
            self.update_hosts_list()
    
    def select_command_file(self):
        """Opens a file dialog to select the commands file."""
        current_path = self.cmd_file_input.text()
        default_dir = os.path.dirname(current_path) if os.path.isdir(os.path.dirname(current_path)) else SCRIPT_DIR
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Commands File", default_dir, "Text Files (*.txt);;All Files (*)"
        )
        
        if file_path:
            self.cmd_file_input.setText(file_path)
            self.update_command_list()

    def update_hosts_list(self):
        """Reloads hosts when the file path changes."""
        new_file_path = self.file_input.text()
        self.host_list_data = self.load_hosts(new_file_path)
        self.populate_list()
        
        self.settings['host_file_path'] = new_file_path
        save_settings(self.settings)
    
    def update_command_list(self):
        """Reloads commands when the file path changes."""
        new_file_path = self.cmd_file_input.text()
        
        # FIX: Update setting path first so populate_command_dropdown uses the new path
        self.settings['command_file_path'] = new_file_path
        
        self.populate_command_dropdown()
        
        save_settings(self.settings)


    def populate_list(self):
        self.list_widget.clear()
        if not self.host_list_data:
            list_item = QListWidgetItem(self.list_widget)
            widget = QLabel("No hosts found in the selected file.")
            widget.setStyleSheet("color: red; font-weight: bold; padding: 5px;")
            list_item.setSizeHint(widget.sizeHint())
            self.list_widget.setItemWidget(list_item, widget)
            return
            
        for item_data in self.host_list_data:
            name = item_data['name']
            checked = item_data.get('checked', True) 
            
            list_item = QListWidgetItem(self.list_widget)
            widget = HostItemWidget(name, checked) 
            list_item.setSizeHint(widget.sizeHint())
            self.list_widget.setItemWidget(list_item, widget)

    def save_current_state(self):
        """Saves current checkbox states and updates application settings."""
        # 1. Update checkbox states
        new_data = []
        for i in range(self.list_widget.count()):
            list_item = self.list_widget.item(i)
            if isinstance(self.list_widget.itemWidget(list_item), HostItemWidget):
                widget = self.list_widget.itemWidget(list_item)
                
                if i < len(self.host_list_data):
                    original_data = self.host_list_data[i]
                    original_data['checked'] = widget.checkbox.isChecked()
                    new_data.append(original_data)
        
        self.host_list_data = new_data

        # 2. Update and save settings
        self.settings['host_file_path'] = self.file_input.text()
        self.settings['command_file_path'] = self.cmd_file_input.text()
        self.settings['remote_user'] = self.user_input.text()
        self.settings['ssh_key_path'] = self.key_input.text() 
        self.settings['selected_command'] = self.command_dropdown.currentText() # Save selected command
        save_settings(self.settings)


    def prompt_and_execute(self):
        
        self.save_current_state()
        
        remote_user = self.settings['remote_user']
        ssh_key_path = self.settings['ssh_key_path']
        selected_command = self.settings['selected_command']
        hosts_to_upgrade = [item['name'] for item in self.host_list_data if item['checked']]

        if not hosts_to_upgrade:
            QMessageBox.information(self, "No Selection", "Please check at least one host.")
            return

        if not selected_command or "No commands found" in selected_command:
            QMessageBox.critical(self, "Command Error", "Please select a valid remote command.")
            return

        self.active_sessions = [s for s in self.active_sessions if s['worker'].isRunning()]

        # ... (validation for user/key/etc)
        if not remote_user or not ssh_key_path:
            QMessageBox.critical(self, "Input Error", "Remote User and SSH Key Path fields cannot be empty.")
            return

        if not os.path.exists(ssh_key_path):
            QMessageBox.critical(self, "Key Error", f"SSH Key not found at: {ssh_key_path}")
            return


        sudo_password, ok = QInputDialog.getText(
            self, "Sudo Password", 
            f"Enter the **sudo** password for user **{remote_user}** on remote hosts (will be injected once):", 
            QLineEdit.EchoMode.Password 
        )

        if not ok or not sudo_password:
            QMessageBox.warning(self, "Cancelled", "Launch cancelled by user.")
            return

        hosts_launched = 0
        for host in hosts_to_upgrade:
            if any(s['host'] == host and s['worker'].isRunning() for s in self.active_sessions):
                print(f"Session for {host} is already active, skipping.")
                continue

            # Pass the selected command to the execution function
            self.execute_interactive_upgrade(host, remote_user, ssh_key_path, sudo_password, selected_command)
            hosts_launched += 1

        QMessageBox.information(
            self, "Sessions Started", 
            f"Launched {hosts_launched} session window(s) to run '{selected_command}'. Please check each window for prompts."
        )


    def execute_interactive_upgrade(self, host, user, ssh_key_path, sudo_password, remote_command):
        """Starts a single interactive worker thread and terminal window for the given host."""
        
        # Pass the command to the TerminalWindow for display
        terminal = TerminalWindow(self, host_name=host, command=remote_command) 
        
        # Pass the command to the Worker
        worker = InteractiveUpgradeWorker(host, user, ssh_key_path, sudo_password, remote_command, terminal.input_queue)
        
        terminal.worker = worker
        
        worker.log_output.connect(terminal.append_log)
        worker.session_error.connect(self.handle_host_error)
        worker.session_complete.connect(self.handle_host_complete)
        
        self.active_sessions.append({
            'host': host,
            'worker': worker,
            'terminal': terminal
        })
        worker.start()
        
        terminal.show()
        terminal.raise_()
        terminal.activateWindow()
            
    def handle_host_error(self, host, error_message):
        QMessageBox.critical(
            self, f"Error on {host}", 
            f"A critical error occurred on **{host}**:\n{error_message}"
        )
        for session in self.active_sessions:
            if session['host'] == host:
                session['terminal'].close()

    def handle_host_complete(self, host):
        QMessageBox.information(
            self, f"Command Complete", 
            f"Command finished successfully on **{host}**."
        )
        
    def closeEvent(self, event):
        """Saves settings when the main window is closed."""
        self.save_current_state()
        event.accept()

# --- Application Setup ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    try:
        app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api='pyqt6')) 
        
        palette = QPalette() 
        palette.setColor(QPalette.ColorRole.Highlight, QColor("darkorange"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(Qt.GlobalColor.black))
        app.setPalette(palette)
    except Exception as e:
        print(f"Could not apply dark theme: {e}") 

    launcher = GobytegoRemoteCommandLauncher()
    launcher.show()
    
    sys.exit(app.exec())
