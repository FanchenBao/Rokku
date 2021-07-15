import time
import subprocess
from pathlib import Path
import re
import logging
import logging.config
import yaml
import csv
import json
from argparse import ArgumentParser, RawDescriptionHelpFormatter
import sys


ROOT = Path('.').absolute().parent
EMIT = ROOT.joinpath('probe_request_injection/emit/emit.sh')
EMITTER_MAP = {
    '7d:fd:97': '06',
    '7d:fd:a6': '09',
    '85:7a:5e': '29',
    '85:78:a5': '30',
    '6e:4e:8a': '40',
}

# set up logger
with open('logger_config.yaml', "r") as f:
    config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)
logger = logging.getLogger("StressTest")


def get_argument_parser() -> ArgumentParser:
    """Set up a parser to parse command line arguments.

    :return: A fresh, unused, ArgumentParser.
    """
    parser = ArgumentParser(
        description='''
        The script to initialize a series of stress test. Sample usage:

        python3 stress_test.py \\
        --start_time 1626379183 \\
        --duration 20 \\
        --max_power 16 \\
        --start_number_emitters 2 \\
        --end_number_emitters 5
        ''',
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--start_time',
        dest='start_time',
        type=int,
        required=True,
        help='REQUIRED. The epoch time when this emitter starts stress test',
    )
    parser.add_argument(
        '--duration',
        dest='duration',
        type=int,
        required=True,
        help='REQUIRED. The amount of time (in seconds) allowed for probe request emission',
    )
    parser.add_argument(
        '--max_power',
        dest='max_power',
        type=int,
        required=True,
        help='REQUIRED. The maximum power to compute probe request emission rate. That is, the highest probe request emission rate is 2^max_power per second. In the stress test, the power goes from 0 to max_power',
    )
    parser.add_argument(
        '--start_number_emitters',
        dest='start_number_emitters',
        type=int,
        required=True,
        help='REQUIRED. The number of emitters (including this emitter) when this emitter starts emitting.'
    )
    parser.add_argument(
        '--end_number_emitters',
        dest='end_number_emitters',
        type=int,
        required=True,
        help='REQUIRED. The number of emitters in the final round of stress test (including this emitter)',
    )
    parser.add_argument(
        '--num_rounds',
        dest='num_rounds',
        type=int,
        required=True,
        help='REQUIRED. The number of rounds per specific number of emitters',
    )
    parser.add_argument(
        '--est_time_per_round',
        dest='est_time_per_round',
        type=int,
        required=True,
        help='REQUIRED. Number of very generous estimated seconds per round of emission. A round is defined as emitting from z^0 to 2^max_power number of probe requests per second',
    )
    return parser


def get_device_mac(interface='eth0') -> str:
    """Get MAC address of the given interface on the device.

    This piece of code is directly referenced from:
    https://www.raspberrypi-spy.co.uk/2012/06/finding-the-mac-address-of-a-raspberry-pi/

    If the interface exists, return its full MAC address as string; otherwise
    return all zeros.

    :param interface:   Web interface whose MAC address to be retrieved.
    :return: A string of MAC address of the given interface, or all zeros.
    """
    try:
        with open(f'/sys/class/net/{interface}/address') as file_obj:
            mac_addr = file_obj.read()
    except Exception:
        mac_addr = '00:00:00:00:00:00'
    mac_addr_len: int = 17
    return mac_addr[:mac_addr_len]


def stress_test(num_emitters: int, rd: int, max_power: int, duration: int) -> None:
    """Conduct stress test.

    We run emission in tmux and record the number of packets emitted at the
    end of each emission.

    :param num_emitters: Number of emitters currently active (including this
        emitter).
    :param rd: The current round. E.g. if rd == 1, we are in the first round.
    :param max_power: The maximum power that we will raise 2 up to for the
        theoretical number of probe requests to emit per second.
    :param duration: The number of seconds each probe request emission at each
        emission rate will last.
    """
    with open(f'stress_test_summary.csv', 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        for power in range(max_power):
            emitter_id = EMITTER_MAP[get_device_mac()[9:]]
            mac = f'{num_emitters}{rd}:{emitter_id}:{power:02}'
            interval = 1 / (2**power)
            subprocess.run(f"{EMIT} -i wlan1 -c 10 --interval {interval} --mac {mac}", shell=True, text=True, capture_output=True)
            start_time = int(time.time() * 1000)
            time.sleep(duration + 1)
            subprocess.run('tmux send-keys -t emit C-C Enter', shell=True, text=True, capture_output=True)
            time.sleep(1)
            p = subprocess.run('tmux capture-pane -p', shell=True, text=True, capture_output=True)
            time.sleep(1)
            subprocess.run('tmux kill-server', shell=True, text=True, capture_output=True)
            match = re.search(r'Sent\s(\d+)\spackets', p.stdout)
            logger.info(f'Interval {interval} complete')
            writer.writerow(
                [num_emitters, rd, interval, emitter_id, match.group(1), start_time, int(time.time() * 1000)],
            )

def main():
    parser: ArgumentParser = get_argument_parser()
    args = parser.parse_args()

    with open(f'stress_test_summary.csv', 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['num_emitters', 'round', 'interval', 'emitter_id', 'emit_count', 'start', 'end'])

    start_times = list(
        range(
            args.start_time,
            args.start_time + (args.end_number_emitters - args.start_number_emitters + 1) * args.est_time_per_round * args.num_rounds,
            args.est_time_per_round,
        ),
    )
    ti = 0  # index to read the start time for the next round
    for num_emitters in range(args.start_number_emitters, args.end_number_emitters + 1):
        logger.info(f'Stress test {num_emitters} emitters')
        for rd in range(1, args.num_rounds + 1):
            logger.info(f'Round {rd} will start at {time.strftime("%a, %d %b %Y %H:%M:%S", time.localtime(start_times[ti]))}')
            while int(time.time()) < start_times[ti]:
                time.sleep(0.1)  # wait for start time
            logger.info(f'Round {rd} STARTS!')
            try:
                stress_test(num_emitters, rd, args.max_power, args.duration)
            except Exception as err:
                logger.error(f'Stress test FAILED. Error message: {err}')
                sys.exit(0)
            logger.info(f'Round {rd} COMPLETES!')
            ti += 1


if __name__ == '__main__':
    main()    