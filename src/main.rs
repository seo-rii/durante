use clap::{Parser, Subcommand};
use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod isolation;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod output;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod runner;

/// Diagnostic software-work measurement; not a sandbox or trusted judge.
#[derive(Parser)]
#[command(version, about)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Collect records only after the entire measured process tree has stopped.
    Collect {
        #[arg(long)]
        dir: PathBuf,
        #[arg(long, value_parser = parse_pid)]
        root_pid: u32,
    },
    /// Run the native counter, reap the process tree, and write a diagnostic report.
    Run {
        /// Trusted DynamoRIO launcher executable.
        #[arg(long)]
        drrun: PathBuf,
        /// Trusted native Durante client shared library.
        #[arg(long)]
        client: PathBuf,
        /// New JSON report file; an existing file is never replaced.
        #[arg(long)]
        report: PathBuf,
        /// Internal capture file provided by the isolation controller.
        #[arg(long, hide = true)]
        report_existing: bool,
        /// Total wall deadline for the root and all descendants, in milliseconds.
        #[arg(long, default_value_t = 30_000, value_parser = parse_timeout)]
        timeout_ms: u64,
        /// Create and retain this new, private directory for lifecycle records.
        #[arg(long)]
        records_dir: Option<PathBuf>,
        /// Retain an automatically created records directory and report its path.
        #[arg(long)]
        keep_records: bool,
        /// Target executable and arguments, passed directly without a shell.
        #[arg(required = true, last = true, num_args = 1..)]
        command: Vec<OsString>,
    },
    /// Run the diagnostic CLI inside a pinned, resource-bounded namespace profile.
    Isolate {
        /// Pinned launcher produced by toolkit/build_isolation.py.
        #[arg(long)]
        launcher: PathBuf,
        #[arg(long)]
        drrun: PathBuf,
        #[arg(long)]
        client: PathBuf,
        /// Input directory copied into the read-only /work guest directory.
        #[arg(long)]
        workspace: PathBuf,
        /// New host-side report; this path is never mounted into the guest.
        #[arg(long)]
        report: PathBuf,
        #[arg(long, default_value_t = 30_000, value_parser = parse_timeout)]
        timeout_ms: u64,
        /// Capture stdout/stderr into this fresh private host directory.
        #[arg(long)]
        output_dir: Option<PathBuf>,
        /// Combined captured-byte limit (default 1 MiB, at most 64 MiB).
        #[arg(long, requires = "output_dir", value_parser = parse_output_limit)]
        output_limit_bytes: Option<u64>,
        #[arg(required = true, last = true, num_args = 1..)]
        command: Vec<OsString>,
    },
}

fn parse_pid(value: &str) -> Result<u32, String> {
    value
        .parse::<u32>()
        .ok()
        .filter(|value| *value > 0 && *value <= i32::MAX as u32)
        .ok_or_else(|| "PID must be a positive signed-32-bit integer".to_owned())
}

fn parse_timeout(value: &str) -> Result<u64, String> {
    value
        .parse::<u64>()
        .ok()
        .filter(|value| (1..=86_400_000).contains(value))
        .ok_or_else(|| "timeout must be between 1 and 86400000 milliseconds".to_owned())
}

fn parse_output_limit(value: &str) -> Result<u64, String> {
    value
        .parse::<u64>()
        .ok()
        .filter(|value| (1..=64 << 20).contains(value))
        .ok_or_else(|| "output limit must be between 1 and 67108864 bytes".to_owned())
}

fn main() {
    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(error)
            if matches!(
                error.kind(),
                clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion
            ) =>
        {
            let _ = error.print();
            std::process::exit(0);
        }
        Err(error) => {
            // Parser diagnostics can quote an unrecognized argument or value.
            // A target argument may contain a credential, so do not print it.
            let hint = match error.kind() {
                clap::error::ErrorKind::UnknownArgument => {
                    "unknown argument; put '--' before the target command"
                }
                clap::error::ErrorKind::MissingRequiredArgument
                | clap::error::ErrorKind::MissingSubcommand => {
                    "required argument or subcommand is missing"
                }
                _ => "invalid command-line option or value",
            };
            eprintln!("durante: {hint}; use --help for usage");
            std::process::exit(2);
        }
    };
    let result = match cli.command {
        Command::Collect { dir, root_pid } => {
            let result = durante::collect::collect(&dir, root_pid);
            let stdout = io::stdout();
            let mut output = stdout.lock();
            match serde_json::to_writer(&mut output, &result)
                .map_err(io::Error::other)
                .and_then(|()| writeln!(output))
            {
                Ok(()) => Ok(if result["valid"] == true && result["complete"] == true {
                    0
                } else {
                    1
                }),
                Err(_) => Err("cannot write collection result".to_owned()),
            }
        }
        Command::Run {
            drrun,
            client,
            report,
            report_existing,
            timeout_ms,
            records_dir,
            keep_records,
            command,
        } => {
            #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
            {
                runner::run(runner::Options {
                    drrun,
                    client,
                    report,
                    report_existing,
                    timeout_ms,
                    records_dir,
                    keep_records,
                    command,
                })
            }
            #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
            {
                let _ = (
                    drrun,
                    client,
                    report,
                    report_existing,
                    timeout_ms,
                    records_dir,
                    keep_records,
                    command,
                );
                Err("durante run requires Linux x86-64".to_owned())
            }
        }
        Command::Isolate {
            launcher,
            drrun,
            client,
            workspace,
            report,
            timeout_ms,
            output_dir,
            output_limit_bytes,
            command,
        } => {
            #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
            {
                isolation::run(isolation::Options {
                    launcher,
                    drrun,
                    client,
                    workspace,
                    report,
                    timeout_ms,
                    output_dir,
                    output_limit_bytes: output_limit_bytes.unwrap_or(output::DEFAULT_LIMIT),
                    command,
                })
            }
            #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
            {
                let _ = (
                    launcher,
                    drrun,
                    client,
                    workspace,
                    report,
                    timeout_ms,
                    command,
                    output_dir,
                    output_limit_bytes,
                );
                Err("durante isolate requires Linux x86-64".to_owned())
            }
        }
    };
    match result {
        Ok(code) => std::process::exit(code),
        Err(reason) => {
            eprintln!("durante: {reason}");
            std::process::exit(1);
        }
    }
}
