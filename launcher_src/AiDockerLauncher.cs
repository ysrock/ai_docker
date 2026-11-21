using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace AiDockerLauncher
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
                    "AI docker 启动失败",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }

            if (!File.Exists(script))
            {
                MessageBox.Show(
                    "找不到启动脚本：\n" + script,
                    "AI docker 启动失败",
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
                    "启动 AI docker 时出错：\n" + ex.Message,
                    "AI docker 启动失败",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }
        }
    }
}
