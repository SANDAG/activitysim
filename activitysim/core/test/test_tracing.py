# ActivitySim
# See full license in LICENSE.txt.
import logging
import os.path

import pandas as pd
import pytest

from activitysim.abm.tables import table_dict
from activitysim.core import inject, tracing, workflow


def close_handlers():

    loggers = logging.Logger.manager.loggerDict
    for name in loggers:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
        logger.setLevel(logging.NOTSET)


def teardown_function(func):
    inject.clear_cache()
    inject.reinject_decorated_tables()


def add_canonical_dirs():

    whale = workflow.Whale()

    configs_dir = os.path.join(os.path.dirname(__file__), "configs")
    whale.add_injectable("configs_dir", configs_dir)

    output_dir = os.path.join(os.path.dirname(__file__), "output")
    whale.add_injectable("output_dir", output_dir)

    whale.initialize_filesystem(
        working_dir=os.path.dirname(__file__),
        configs_dir=(configs_dir,),
        output_dir=output_dir,
    )

    return whale


def test_config_logger(capsys):

    whale = add_canonical_dirs()

    whale.logging.config_logger()

    logger = logging.getLogger("activitysim")

    file_handlers = [h for h in logger.handlers if type(h) is logging.FileHandler]
    assert len(file_handlers) == 1
    asim_logger_baseFilename = file_handlers[0].baseFilename

    print("handlers:", logger.handlers)

    logger.info("test_config_logger")
    logger.info("log_info")
    logger.warning("log_warn1")

    out, err = capsys.readouterr()

    # don't consume output
    print(out)

    assert "could not find conf file" not in out
    assert "log_warn1" in out
    assert "log_info" not in out

    close_handlers()

    logger = logging.getLogger(__name__)
    logger.warning("log_warn2")

    with open(asim_logger_baseFilename, "r") as content_file:
        content = content_file.read()
        print(content)
    assert "log_warn1" in content
    assert "log_warn2" not in content


def test_print_summary(capsys):

    whale = add_canonical_dirs()

    whale.logging.config_logger()

    tracing.print_summary(
        "label", df=pd.DataFrame(), describe=False, value_counts=False
    )

    out, err = capsys.readouterr()

    # don't consume output
    print(out)

    assert "print_summary neither value_counts nor describe" in out

    close_handlers()


def test_register_households(capsys):

    whale = add_canonical_dirs()
    whale.load_settings()

    whale.logging.config_logger()

    df = pd.DataFrame({"zort": ["a", "b", "c"]}, index=[1, 2, 3])

    whale.tracing.traceable_tables = ["households"]
    whale.settings.trace_hh_id = 5

    whale.tracing.register_traceable_table("households", df)
    out, err = capsys.readouterr()
    # print out   # don't consume output

    assert "Can't register table 'households' without index name" in out

    df.index.name = "household_id"
    whale.tracing.register_traceable_table("households", df)
    out, err = capsys.readouterr()
    # print out   # don't consume output

    # should warn that household id not in index
    assert "trace_hh_id 5 not in dataframe" in out

    close_handlers()


def test_register_tours(capsys):

    whale = add_canonical_dirs().load_settings()

    whale.logging.config_logger()

    whale.tracing.traceable_tables = ["households", "tours"]

    # in case another test injected this
    whale.add_injectable("trace_tours", [])
    whale.settings.trace_hh_id = 3

    tours_df = pd.DataFrame({"zort": ["a", "b", "c"]}, index=[10, 11, 12])
    tours_df.index.name = "tour_id"

    whale.tracing.register_traceable_table("tours", tours_df)

    out, err = capsys.readouterr()
    assert (
        "can't find a registered table to slice table 'tours' index name 'tour_id'"
        in out
    )

    whale.add_injectable("trace_hh_id", 3)
    households_df = pd.DataFrame({"dzing": ["a", "b", "c"]}, index=[1, 2, 3])
    households_df.index.name = "household_id"
    whale.tracing.register_traceable_table("households", households_df)

    whale.tracing.register_traceable_table("tours", tours_df)

    out, err = capsys.readouterr()
    # print out  # don't consume output
    assert "can't find a registered table to slice table 'tours'" in out

    tours_df["household_id"] = [1, 5, 3]

    whale.tracing.register_traceable_table("tours", tours_df)

    out, err = capsys.readouterr()
    print(out)  # don't consume output

    # should be tracing tour with tour_id 3
    traceable_table_ids = whale.tracing.traceable_table_ids
    assert traceable_table_ids["tours"] == [12]

    close_handlers()


def test_write_csv(capsys):

    whale = add_canonical_dirs()

    whale.logging.config_logger()

    # should complain if df not a DataFrame or Series
    tracing.write_csv(whale, df="not a df or series", file_name="baddie")

    out, err = capsys.readouterr()

    print(out)  # don't consume output

    assert "unexpected type" in out

    close_handlers()


def test_slice_ids():

    df = pd.DataFrame({"household_id": [1, 2, 3]}, index=[11, 12, 13])

    # slice by named column
    sliced_df = tracing.slice_ids(df, [1, 3, 6], column="household_id")
    assert len(sliced_df.index) == 2

    # slice by index
    sliced_df = tracing.slice_ids(df, [6, 12], column=None)
    assert len(sliced_df.index) == 1

    # attempt to slice by non-existent column
    with pytest.raises(RuntimeError) as excinfo:
        sliced_df = tracing.slice_ids(df, [5, 6], column="baddie")
    assert "slice_ids slicer column 'baddie' not in dataframe" in str(excinfo.value)


def test_basic(capsys):

    close_handlers()

    # configs_dir = os.path.join(os.path.dirname(__file__), "configs")
    # inject.add_injectable("configs_dir", configs_dir)
    #
    # output_dir = os.path.join(os.path.dirname(__file__), "output")
    # inject.add_injectable("output_dir", output_dir)

    whale = add_canonical_dirs()

    # remove existing handlers or basicConfig is a NOP
    logging.getLogger().handlers = []

    whale.logging.config_logger(basic=True)

    logger = logging.getLogger()
    file_handlers = [h for h in logger.handlers if type(h) is logging.FileHandler]
    assert len(file_handlers) == 0

    logger = logging.getLogger("activitysim")

    logger.info("test_basic")
    logger.debug("log_debug")
    logger.info("log_info")
    logger.warning("log_warn")

    out, err = capsys.readouterr()

    # don't consume output
    print(out)

    assert "log_warn" in out
    assert "log_info" in out
    assert "log_debug" not in out

    close_handlers()
