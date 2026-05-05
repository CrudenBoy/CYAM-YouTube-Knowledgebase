import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import subprocess
import threading
import sys
import os

class VideoIndexerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("CYAM Video Indexer")
        self.root.geometry("600x400")
        self.root.configure(padx=20, pady=20)

        # Title
        tk.Label(root, text="CYAM Video Indexer Pipeline", font=("Helvetica", 16, "bold")).pack(pady=(0, 20))

        # File Selection Frame
        frame = tk.Frame(root)
        frame.pack(fill=tk.X, pady=10)

        self.btn_select = tk.Button(frame, text="Select batch_manifest.csv", command=self.select_file, width=25)
        self.btn_select.pack(side=tk.LEFT, padx=5)

        self.lbl_file = tk.Label(frame, text="No file selected...", fg="gray")
        self.lbl_file.pack(side=tk.LEFT, padx=5)

        self.btn_run = tk.Button(root, text="🚀 Run Indexer", command=self.run_pipeline, state=tk.DISABLED, bg="#007aff", fg="white", font=("Helvetica", 14))
        self.btn_run.pack(pady=15, fill=tk.X)

        # Console Output
        tk.Label(root, text="Terminal Output:").pack(anchor=tk.W)
        self.console = scrolledtext.ScrolledText(root, height=12, bg="black", fg="white", font=("Courier", 12))
        self.console.pack(fill=tk.BOTH, expand=True)
        
        self.selected_file = None

    def select_file(self):
        filepath = filedialog.askopenfilename(
            title="Select Batch Manifest",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*"))
        )
        if filepath:
            self.selected_file = filepath
            self.lbl_file.config(text=os.path.basename(filepath), fg="black")
            self.btn_run.config(state=tk.NORMAL)
            self.log(f"Selected file: {filepath}")

    def log(self, message):
        self.console.insert(tk.END, message + "\n")
        self.console.see(tk.END)

    def run_pipeline(self):
        if not self.selected_file:
            return

        self.btn_run.config(state=tk.DISABLED, text="⏳ Processing... Please wait")
        self.btn_select.config(state=tk.DISABLED)
        self.console.delete(1.0, tk.END)
        self.log(f"Starting pipeline using: {self.selected_file}\n")
        
        # Run in a separate thread so UI doesn't freeze
        thread = threading.Thread(target=self.execute_script)
        thread.daemon = True
        thread.start()

    def execute_script(self):
        # We assume index_video.py is in the same directory as app.py
        script_dir = os.path.dirname(os.path.abspath(__file__))
        index_script = os.path.join(script_dir, "index_video.py")
        
        # Default output name
        out_csv = os.path.join(os.path.dirname(self.selected_file), "final_video_index.csv")

        cmd = [
            sys.executable, index_script,
            "--batch", self.selected_file,
            "--out", out_csv
        ]

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        # Read output line by line and stream to UI
        for line in process.stdout:
            self.root.after(0, self.log, line.strip())

        process.wait()
        
        if process.returncode == 0:
            self.root.after(0, self.log, f"\n✅ SUCCESS! Output saved to: {out_csv}")
            self.root.after(0, messagebox.showinfo, "Success", "Video indexing is complete!")
        else:
            self.root.after(0, self.log, f"\n❌ FAILED with error code {process.returncode}")
            self.root.after(0, messagebox.showerror, "Error", "The pipeline failed. Check the terminal output for details.")

        # Reset UI
        self.root.after(0, self.reset_ui)

    def reset_ui(self):
        self.btn_run.config(state=tk.NORMAL, text="🚀 Run Indexer")
        self.btn_select.config(state=tk.NORMAL)

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoIndexerApp(root)
    root.mainloop()
