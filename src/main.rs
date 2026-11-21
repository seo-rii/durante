use clap::{Parser, Subcommand, ValueEnum};
use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod doctor;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod isolation;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod output;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod runner;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod work_budget;

/// Diagnostic software-work measurement; not a sandbox or trusted judge.
#[derive(Parser)]
#[command(version, about)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Inspect prerequisites without launching targets or changing host policy.
    Doctor {
        #[arg(long, value_enum, default_value_t = DoctorProfile::Run)]
        profile: DoctorProfile,
        #[arg(long)]
        drrun: Option<PathBuf>,
        #[arg(long)]
        client: Option<PathBuf>,
        /// Pinned launcher and adjacent manifest, required for the isolate profile.
        #[arg(long)]
        launcher: Option<PathBuf>,
    },
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
        /// Stop before executing a forbidden instruction; repeat for both policies.
        #[arg(long = "disallow-cpu-feature", value_parser = ["avx", "simd"])]
        disallowed_cpu_features: Vec<String>,
        /// Report one fixed virtual processor to every application CPUID/XGETBV.
        #[arg(long = "cpuid-profile", value_parser = ["x86-64-v1", "x86-64-v2"])]
        cpuid_profile: Option<String>,
        /// Also count the static per-block latency bound (diagnostic, version 1).
        #[arg(long = "latency-bound")]
        latency_bound: bool,
        /// Also count statically sized application memory operand bytes (diagnostic, version 1).
        #[arg(long, requires = "latency_bound")]
        memory_operand_bytes: bool,
        /// Stop after more than this many software-work units (instructions + MOVS/STOS bytes).
        #[arg(long)]
        work_budget: Option<u64>,
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
        /// Forward the native instruction policy into the isolated run.
        #[arg(long = "disallow-cpu-feature", value_parser = ["avx", "simd"])]
        disallowed_cpu_features: Vec<String>,
        /// Forward the fixed virtual processor identity into the isolated run.
        #[arg(long = "cpuid-profile", value_parser = ["x86-64-v1", "x86-64-v2"])]
        cpuid_profile: Option<String>,
        /// Forward the diagnostic software-work budget into the isolated run.
        #[arg(long)]
        work_budget: Option<u64>,
        /// Forward the latency-bound counter request into the isolated run.
        #[arg(long = "latency-bound")]
        latency_bound: bool,
        /// Also count statically sized application memory operand bytes (diagnostic, version 1).
        #[arg(long, requires = "latency_bound")]
        memory_operand_bytes: bool,
        #[arg(required = true, last = true, num_args = 1..)]
        command: Vec<OsString>,
    },
}

#[derive(Clone, Copy, ValueEnum)]
enum DoctorProfile {
    Run,
    Isolate,
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
    let mut cli = match Cli::try_parse() {
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
    if let Command::Run {
        disallowed_cpu_features,
        ..
    }
    | Command::Isolate {
        disallowed_cpu_features,
        ..
    } = &mut cli.command
    {
        disallowed_cpu_features.sort_unstable();
        if disallowed_cpu_features.len() > 2
            || disallowed_cpu_features
                .windows(2)
                .any(|pair| pair[0] == pair[1])
        {
            eprintln!("durante: duplicate or excessive CPU feature restrictions");
            std::process::exit(2);
        }
    }
    let result = match cli.command {
        Command::Doctor {
            profile,
            drrun,
            client,
            launcher,
        } => {
            #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
            {
                doctor::run(doctor::Options {
                    isolation: matches!(profile, DoctorProfile::Isolate),
                    drrun,
                    client,
                    launcher,
                })
            }
            #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
            {
                let _ = (profile, drrun, client, launcher);
                Err("durante doctor requires Linux x86-64".to_owned())
            }
        }
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
            disallowed_cpu_features,
            cpuid_profile,
            latency_bound,
            memory_operand_bytes,
            work_budget,
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
                    disallowed_cpu_features,
                    cpuid_profile,
                    latency_bound,
                    memory_operand_bytes,
                    work_budget,
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
                    disallowed_cpu_features,
                    cpuid_profile,
                    latency_bound,
                    memory_operand_bytes,
                    work_budget,
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
            disallowed_cpu_features,
            cpuid_profile,
            latency_bound,
            memory_operand_bytes,
            work_budget,
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
                    disallowed_cpu_features,
                    cpuid_profile,
                    latency_bound,
                    memory_operand_bytes,
                    work_budget,
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
                    disallowed_cpu_features,
                    cpuid_profile,
                    latency_bound,
                    memory_operand_bytes,
                    work_budget,
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
