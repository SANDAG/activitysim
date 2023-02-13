# ActivitySim
# See full license in LICENSE.txt.
import argparse
import importlib
import logging
import os
import sys
import warnings

import numpy as np

from activitysim.core import chunk, config, inject, mem, tracing, workflow
from activitysim.core.configuration import FileSystem, Settings

logger = logging.getLogger(__name__)


INJECTABLES = [
    "data_dir",
    "configs_dir",
    "output_dir",
    "settings_file_name",
    "imported_extensions",
]


def add_run_args(parser, multiprocess=True):
    """Run command args"""
    parser.add_argument(
        "-w",
        "--working_dir",
        type=str,
        metavar="PATH",
        help="path to example/project directory (default: %s)" % os.getcwd(),
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        action="append",
        metavar="PATH",
        help="path to config dir",
    )
    parser.add_argument(
        "-o", "--output", type=str, metavar="PATH", help="path to output dir"
    )
    parser.add_argument(
        "-d",
        "--data",
        type=str,
        action="append",
        metavar="PATH",
        help="path to data dir",
    )
    parser.add_argument(
        "-r", "--resume", type=str, metavar="STEPNAME", help="resume after step"
    )
    parser.add_argument(
        "-p", "--pipeline", type=str, metavar="FILE", help="pipeline file name"
    )
    parser.add_argument(
        "-s", "--settings_file", type=str, metavar="FILE", help="settings file name"
    )
    parser.add_argument(
        "-g", "--chunk_size", type=int, metavar="BYTES", help="chunk size"
    )
    parser.add_argument(
        "--chunk_training_mode",
        type=str,
        help="chunk training mode, one of [training, adaptive, production, disabled]",
    )
    parser.add_argument(
        "--households_sample_size", type=int, metavar="N", help="households sample size"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Do not limit process to one thread. "
        "Can make single process runs faster, "
        "but will cause thrashing on MP runs.",
    )
    parser.add_argument(
        "-e",
        "--ext",
        type=str,
        action="append",
        metavar="PATH",
        help="Package of extension modules to load. Use of this option is not "
        "generally secure.",
    )

    if multiprocess:
        parser.add_argument(
            "-m",
            "--multiprocess",
            default=False,
            const=-1,
            metavar="(N)",
            nargs="?",
            type=int,
            help="run multiprocess. Adds configs_mp settings "
            "by default as the first config directory, but only if it is found"
            "and is not already explicitly included elsewhere in the list of "
            "configs. Optionally give a number of processes greater than 1, "
            "which will override the number of processes written in settings file.",
        )


def validate_injectable(whale: workflow.Whale, name, make_if_missing=False):
    try:
        dir_paths = whale.context.get_formatted(name)
        # dir_paths = whale.get_injectable(name)
    except RuntimeError:
        # injectable is missing, meaning is hasn't been explicitly set
        # and defaults cannot be found.
        sys.exit(
            f"Error({name}): please specify either a --working_dir "
            "containing 'configs', 'data', and 'output' folders "
            "or all three of --config, --data, and --output"
        )

    dir_paths = [dir_paths] if isinstance(dir_paths, str) else dir_paths

    for dir_path in dir_paths:
        if not os.path.exists(dir_path):
            if make_if_missing:
                os.makedirs(dir_path)
            else:
                sys.exit("Could not find %s '%s'" % (name, os.path.abspath(dir_path)))

    return dir_paths


def handle_standard_args(whale: workflow.Whale, args, multiprocess=True):
    def inject_arg(name, value):
        assert name in INJECTABLES
        whale.context[name] = value

    if args.working_dir:
        # activitysim will look in the current working directory for
        # 'configs', 'data', and 'output' folders by default
        os.chdir(args.working_dir)

    if args.ext:
        for e in args.ext:
            basepath, extpath = os.path.split(e)
            if not basepath:
                basepath = "."
            sys.path.insert(0, os.path.abspath(basepath))
            try:
                importlib.import_module(extpath)
            except ImportError as err:
                logger.exception("ImportError")
                raise
            except Exception as err:
                logger.exception(f"Error {err}")
                raise
            finally:
                del sys.path[0]
        inject_arg("imported_extensions", args.ext)
    else:
        inject_arg("imported_extensions", ())

    # settings_file_name should be cached or else it gets squashed by config.py
    # if args.settings_file:
    #     inject_arg("settings_file_name", args.settings_file)
    #
    # if args.config:
    #     inject_arg("configs_dir", args.config)
    #
    # if args.data:
    #     inject_arg("data_dir", args.data)
    #
    # if args.output:
    #     inject_arg("output_dir", args.output)

    whale.filesystem = FileSystem.parse_args(args)

    # read settings file
    raw_settings = whale.filesystem.read_settings_file(
        whale.filesystem.settings_file_name,
        mandatory=True,
        include_stack=False,
    )

    # the settings can redefine the cache directories.
    cache_dir = raw_settings.pop("cache_dir", None)
    if cache_dir:
        whale.filesystem.cache_dir = cache_dir
    whale.settings = Settings.parse_obj(raw_settings)

    extra_settings = set(whale.settings.__dict__) - set(Settings.__fields__)

    if extra_settings:
        warnings.warn(
            "Writing arbitrary model values as top-level key in settings.yaml "
            "is deprecated, make them sub-keys of `other_settings` instead.",
            DeprecationWarning,
        )
        logger.warning(f"Found the following unexpected settings:")
        if whale.settings.other_settings is None:
            whale.settings.other_settings = {}
        for k in extra_settings:
            logger.warning(f" - {k}")
            whale.settings.other_settings[k] = getattr(whale.settings, k)
            delattr(whale.settings, k)

    if args.multiprocess:
        if "configs_mp" not in whale.filesystem.configs_dir:
            # when triggering multiprocessing from command arguments,
            # add 'configs_mp' as the first config directory, but only
            # if it exists, and it is not already explicitly included
            # in the set of config directories.
            if not whale.filesystem.get_working_subdir("configs_mp").exists():
                logger.warning("could not find 'configs_mp'. skipping...")
            else:
                logger.info("adding 'configs_mp' to config_dir list...")
                whale.filesystem.configs_dir = (
                    "configs_mp",
                ) + whale.filesystem.configs_dir

        whale.settings.multiprocess = True
        if args.multiprocess > 1:
            # setting --multiprocess to just 1 implies using the number of
            # processes discovered in the configs file, while setting to more
            # than 1 explicitly overrides that setting
            whale.settings.num_processes = args.multiprocess

    if args.chunk_size:
        whale.settings.chunk_size = int(args.chunk_size)
        # config.override_setting("chunk_size", int(args.chunk_size))
    if args.chunk_training_mode is not None:
        whale.settings.chunk_training_mode = args.chunk_training_mode
        # config.override_setting("chunk_training_mode", args.chunk_training_mode)
    if args.households_sample_size is not None:
        whale.settings.households_sample_size = args.households_sample_size
        # config.override_setting("households_sample_size", args.households_sample_size)

    # for injectable in ["configs_dir", "data_dir", "output_dir"]:
    #     validate_injectable(
    #         whale, injectable, make_if_missing=(injectable == "output_dir")
    #     )

    if args.pipeline:
        whale.filesystem.pipeline_file_name = args.pipeline

    if args.resume:
        whale.settings.resume_after = args.resume

    return whale


def cleanup_output_files(whale: workflow.Whale):
    tracing.delete_trace_files(whale)

    csv_ignore = []
    if whale.settings.memory_profile:
        # memory profiling is opened potentially before `cleanup_output_files`
        # is called, but we want to leave any (newly created) memory profiling
        # log files that may have just been created.
        mem_prof_log = whale.get_log_file_path("memory_profile.csv")
        csv_ignore.append(mem_prof_log)

    tracing.delete_output_files(whale, "h5")
    tracing.delete_output_files(whale, "csv", ignore=csv_ignore)
    tracing.delete_output_files(whale, "txt")
    tracing.delete_output_files(whale, "yaml")
    tracing.delete_output_files(whale, "prof")
    tracing.delete_output_files(whale, "omx")


def run(args):
    """
    Run the models. Specify a project folder using the '--working_dir' option,
    or point to the config, data, and output folders directly with
    '--config', '--data', and '--output'. Both '--config' and '--data' can be
    specified multiple times. Directories listed first take precedence.

    returns:
        int: sys.exit exit code
    """

    whale = workflow.Whale()

    # register abm steps and other abm-specific injectables
    # by default, assume we are running activitysim.abm
    # other callers (e.g. populationsim) will have to arrange to register their own steps and injectables
    # (presumably) in a custom run_simulation.py instead of using the 'activitysim run' command
    if not inject.is_injectable("preload_injectables"):
        # register abm steps and other abm-specific injectables
        from activitysim import abm  # noqa: F401

    whale.config_logger(basic=True)
    whale = handle_standard_args(whale, args)  # possibly update injectables

    if whale.settings.rotate_logs:
        config.rotate_log_directory(whale=whale)

    if whale.settings.memory_profile and not whale.settings.multiprocess:
        # Memory sidecar is only useful for single process runs
        # multiprocess runs log memory usage without blocking in the controlling process.
        mem_prof_log = whale.get_log_file_path("memory_profile.csv")
        from ..core.memory_sidecar import MemorySidecar

        memory_sidecar_process = MemorySidecar(mem_prof_log)
    else:
        memory_sidecar_process = None

    # legacy support for run_list setting nested 'models' and 'resume_after' settings
    # if whale.settings.run_list:
    #     warnings.warn(
    #         "Support for 'run_list' settings group will be removed.\n"
    #         "The run_list.steps setting is renamed 'models'.\n"
    #         "The run_list.resume_after setting is renamed 'resume_after'.\n"
    #         "Specify both 'models' and 'resume_after' directly in settings config file.",
    #         FutureWarning,
    #     )
    #     run_list = whale.settings.run_list
    #     if "steps" in run_list:
    #         assert not config.setting(
    #             "models"
    #         ), f"Don't expect 'steps' in run_list and 'models' as stand-alone setting!"
    #         config.override_setting("models", run_list["steps"])
    #
    #     if "resume_after" in run_list:
    #         assert not config.setting(
    #             "resume_after"
    #         ), f"Don't expect 'resume_after' both in run_list and as stand-alone setting!"
    #         config.override_setting("resume_after", run_list["resume_after"])

    # If you provide a resume_after argument to pipeline.run
    # the pipeline manager will attempt to load checkpointed tables from the checkpoint store
    # and resume pipeline processing on the next submodel step after the specified checkpoint
    resume_after = whale.settings.resume_after

    # cleanup if not resuming
    if not resume_after:
        cleanup_output_files(whale)
    elif whale.settings.cleanup_trace_files_on_resume:
        tracing.delete_trace_files(whale)

    whale.config_logger(basic=False)  # update using possibly new logging configs
    config.filter_warnings(whale)
    logging.captureWarnings(capture=True)

    # directories
    for k in ["configs_dir", "settings_file_name", "data_dir", "output_dir"]:
        logger.info("SETTING %s: %s" % (k, getattr(whale.filesystem, k, None)))

    log_settings = whale.settings.log_settings
    for k in log_settings:
        logger.info("SETTING %s: %s" % (k, getattr(whale.settings, k, None)))

    # OMP_NUM_THREADS: openmp
    # OPENBLAS_NUM_THREADS: openblas
    # MKL_NUM_THREADS: mkl
    for env in [
        "MKL_NUM_THREADS",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMBA_NUM_THREADS",
    ]:
        logger.info(f"ENV {env}: {os.getenv(env)}")

    np_info_keys = [
        "atlas_blas_info",
        "atlas_blas_threads_info",
        "atlas_info",
        "atlas_threads_info",
        "blas_info",
        "blas_mkl_info",
        "blas_opt_info",
        "lapack_info",
        "lapack_mkl_info",
        "lapack_opt_info",
        "mkl_info",
    ]

    for cfg_key in np_info_keys:
        info = np.__config__.get_info(cfg_key)
        if info:
            for info_key in ["libraries"]:
                if info_key in info:
                    logger.info(f"NUMPY {cfg_key} {info_key}: {info[info_key]}")

    t0 = tracing.print_elapsed_time()

    try:
        if whale.settings.multiprocess:
            logger.info("run multiprocess simulation")

            from activitysim.core import mp_tasks

            injectables = {k: whale.get_injectable(k) for k in INJECTABLES}
            mp_tasks.run_multiprocess(whale, injectables)

            assert not whale.is_open

            if whale.settings.cleanup_pipeline_after_run:
                whale.cleanup_pipeline()

        else:
            logger.info("run single process simulation")

            whale.run(
                models=whale.settings.models,
                resume_after=resume_after,
                memory_sidecar_process=memory_sidecar_process,
            )

            if whale.settings.cleanup_pipeline_after_run:
                whale.cleanup_pipeline()  # has side effect of closing open pipeline
            else:
                whale.close_pipeline()

            mem.log_global_hwm()  # main process
    except Exception:
        # log time until error and the error traceback
        tracing.print_elapsed_time("all models until this error", t0)
        logger.exception("activitysim run encountered an unrecoverable error")
        raise

    chunk.consolidate_logs(whale)
    mem.consolidate_logs(whale)

    from ..core.flow import TimeLogger

    TimeLogger.aggregate_summary(logger)

    tracing.print_elapsed_time("all models", t0)

    if memory_sidecar_process:
        memory_sidecar_process.stop()

    return 0


if __name__ == "__main__":
    from activitysim import abm  # register injectables  # noqa: F401

    parser = argparse.ArgumentParser()
    add_run_args(parser)
    args = parser.parse_args()
    sys.exit(run(args))
