#!/usr/bin/env python

import argparse
import cmath
import csv
import os
import os.path
import re
import subprocess
import sys

OUTPUT_FILES = ['output.csv', 'output.tab']

# these columns are either Vendor specific or otherwise not important.
IGNORABLE_COLS = ('saveper', 'initial_time', 'final_time', 'time_step', 'time', 'months', 'final time', 'intial time', 'time step')

# from rainbow
def make_reporter(verbosity, quiet, filelike):
    "Returns a function suitible for logging use."
    if not quiet:
        def report(level, msg, *args):
            "Log if the specified severity is <= the initial verbosity."
            if level <= verbosity:
                if len(args):
                    filelike.write(msg % args + '\n')
                else:
                    filelike.write('%s\n' % (msg,))
    else:
        def report(level, msg, *args):
            "/dev/null logger."
            pass
    return report

ERROR = 0
WARN = 1
INFO = 2
DEBUG = 3
log = make_reporter(DEBUG, False, sys.stderr)

def isclose(a,
            b,
            rel_tol=1e-9,
            abs_tol=0.0,
            method='weak'):
    """
    returns True if a is close in value to b. False otherwise
    :param a: one of the values to be tested
    :param b: the other value to be tested
    :param rel_tol=1e-8: The relative tolerance -- the amount of error
                         allowed, relative to the magnitude of the input
                         values.
    :param abs_tol=0.0: The minimum absolute tolerance level -- useful for
                        comparisons to zero.
    :param method: The method to use. options are:
                  "asymmetric" : the b value is used for scaling the tolerance
                  "strong" : The tolerance is scaled by the smaller of
                             the two values
                  "weak" : The tolerance is scaled by the larger of
                           the two values
                  "average" : The tolerance is scaled by the average of
                              the two values.
    NOTES:
    -inf, inf and NaN behave similar to the IEEE 754 standard. That
    -is, NaN is not close to anything, even itself. inf and -inf are
    -only close to themselves.
    Complex values are compared based on their absolute value.
    The function can be used with Decimal types, if the tolerance(s) are
    specified as Decimals::
      isclose(a, b, rel_tol=Decimal('1e-9'))
    See PEP-0485 for a detailed description

    Copyright: Christopher H. Barker
    License: Apache License 2.0 http://opensource.org/licenses/apache2.0.php
    """
    if method not in ("asymmetric", "strong", "weak", "average"):
        raise ValueError('method must be one of: "asymmetric",'
                         ' "strong", "weak", "average"')

    if rel_tol < 0.0 or abs_tol < 0.0:
        raise ValueError('error tolerances must be non-negative')

    if a == b:  # short-circuit exact equality
        return True
    # use cmath so it will work with complex or float
    if cmath.isinf(a) or cmath.isinf(b):
        # This includes the case of two infinities of opposite sign, or
        # one infinity and one finite number. Two infinities of opposite sign
        # would otherwise have an infinite relative tolerance.
        return False
    diff = abs(b - a)
    if method == "asymmetric":
        return (diff <= abs(rel_tol * b)) or (diff <= abs_tol)
    elif method == "strong":
        return (((diff <= abs(rel_tol * b)) and
                 (diff <= abs(rel_tol * a))) or
                (diff <= abs_tol))
    elif method == "weak":
        return (((diff <= abs(rel_tol * b)) or
                 (diff <= abs(rel_tol * a))) or
                (diff <= abs_tol))
    elif method == "average":
        return ((diff <= abs(rel_tol * (a + b) / 2) or
                 (diff <= abs_tol)))
    else:
        raise ValueError('method must be one of:'
                         ' "asymmetric", "strong", "weak", "average"')

def slurp(file_name, encoding='utf-8'):
    with open(file_name, 'r', encoding=encoding) as f:
        return f.read().strip()

def load_csv(f, delimiter=','):
    result = []
    reader = csv.reader(f, delimiter=delimiter)
    header = next(reader)
    for i in range(len(header)):
        result.append([header[i]])
    for row in reader:
        for i in range(len(row)):
            result[i].append(row[i])
    series = {}
    for i in range(len(result)):
        series[result[i][0]] = result[i][1:]
    return series

NAME_RE = re.compile(' +')

def e_name(n):
    return NAME_RE.sub('_', n)

def read_data(data):
    ins = data.lower().splitlines()
    ins[0] = e_name(ins[0].strip())
    if '\t' in ins[0]:
        delimiter = '\t'
    else:
        delimiter = ','
    return load_csv(ins, delimiter)

def compare(reference, simulated, display_limit=-1):
    '''
    Compare two data files for equivalence.
    '''
    time = reference['time']
    steps = len(time)
    err = False
    displayed = 0
    for i in range(steps):
        for n, series in list(reference.items()):
            if n not in simulated:
                if n in IGNORABLE_COLS:
                    continue
                if display_limit >= 0 and displayed < display_limit:
                    log(ERROR, 'missing column %s in second file', n)
                    displayed += 1
                break
            if len(reference[n]) != len(simulated[n]):
                if display_limit >= 0 and displayed < display_limit:
                    log(ERROR, 'len mismatch for %s (%d vs %d)',
                        n, len(reference[n]), len(simulated[n]))
                    displayed += 1
                err = True
                break
            # Vensim only outputs constants in step 0, carry that value forward
            if series[i] == '':
                series[i] = series[i-1]
            ref = float(series[i])
            sim = float(simulated[n][i])
            around_zero = isclose(ref, 0, abs_tol=1e-04) and isclose(sim, 0, abs_tol=1e-04)
            if not around_zero and not isclose(ref, sim, rel_tol=1e-3):
                if display_limit >= 0 and displayed < display_limit:
                    log(ERROR, 'time %s mismatch in %s (%s != %s)', time[i], n, ref, sim)
                    displayed += 1
                err = True
    return err

def run_cmd(cmd):
    '''
    Runs a shell command, waits for it to complete, and returns stdout.
    '''
    call = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    out, err = call.communicate()
    return (call.returncode, out, err)


def run_test(cmd, limit, model_suffix, model_dir):
    err = False

    models = [f for f in os.listdir(model_dir) if f.endswith(model_suffix)]
    if not models:
        return err

    for m in models:
        model_path = os.path.join(model_dir, m)

        log(DEBUG, '  RTEST %s', model_path)
        if cmd == 'stella':
            err, mdata, cmd_stderr = run_cmd('cat %s/output_stella.csv' % (model_dir));
        else :
            err, mdata, cmd_stderr = run_cmd('%s %s' % (cmd, model_path))

        if err:
            log(ERROR, '%s failed: %s', cmd, cmd_stderr)
            continue
        elif cmd_stderr:
            # if there was any, always pass stderr through
            log(ERROR, '%s', cmd_stderr)

        sim = read_data(mdata.decode('utf-8'))

        count = 0
        ref = None

        while ref is None and count < len(OUTPUT_FILES):
            output_path = os.path.join(model_dir, OUTPUT_FILES[count])

            if os.path.exists(output_path):
                try:
                    ref = read_data(slurp(output_path))
                except UnicodeDecodeError:
                    ref = read_data(slurp(output_path, 'latin-1'))

            count += 1

        if ref is None:
            log(ERROR, 'Could not find an expected result in directory %s', model_dir)
            err = True
        else:
            err |= compare(ref, sim, display_limit=limit)

    return err


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-e', '--ext', default='xmile',
                        help='file extension of model to test, such as xmile or mdl')
    parser.add_argument('-l', '--limit', default=10, type=int,
                        help='number of lines of comparison errors to display per ' +
                        'model, negative to disable')
    parser.add_argument('-x', '--exclude', help='regex of directories to exclude')
    parser.add_argument('CMD', help='command to run that will output model results to stdout, or stella which will compare output.csv to output_stella.csv')
    parser.add_argument('DIR', help='path to test-models directory')
    args = parser.parse_args()

    model_suffix = '.' + args.ext

    err = False

    exclude_regex = None
    if args.exclude:
        exclude_regex = re.compile(args.exclude)

    dirs = [args.DIR]
    while dirs:
        d = dirs.pop()
        for dent in os.listdir(d):
            full_path = os.path.join(d, dent)
            if not dent.startswith('.') and os.path.isdir(full_path):
                dirs.append(full_path)
        if exclude_regex and exclude_regex.search(d):
            continue

        err |= run_test(args.CMD, args.limit, model_suffix, d)

    return err

if __name__ == '__main__':
    exit(main())
