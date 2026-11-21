using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace AiDokerLauncher
{
    internal static class Program
    {
        [STAThread]
        private static int Main()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string pythonw = Path.Combine(baseDir, ".venv", "Scripts", "pythonw.exe");
            string script = Path.Combine(baseDir, "start_dock.pyw");

            if (!File.Exists(pythonw))
            {
                MessageBox.Show(
                    "找不到虚拟环境解释器：\n" + pythonw,
                    "AI Doker 启动失败",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }

            if (!File.Exists(script))
            {
                MessageBox.Show(
                    "找不到启动脚本：\n" + script,
                    "AI Doker 启动失败",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }

            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = pythonw,
                    Arguments = "\"" + script + "\"",
                    WorkingDirectory = baseDir,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                };
                Process.Start(psi);
                return 0;
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "启动 AI Doker 时出错：\n" + ex.Message,
                    "AI Doker 启动失败",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }
        }
    }
}
