#!/bin/bash -l
#SBATCH --partition=long
#SBATCH --nodes=1 --ntasks-per-node=!!##CORES##!! --cpus-per-task=1 --exclusive
#SBATCH --mem=60G
#SBATCH --job-name=!!##JOBID##!!
#SBATCH --output=!!##RUNDIR##!!/%x-%j.out
#SBATCH --error=!!##RUNDIR##!!/%x-%j.err
#SBATCH --mail-type=ALL
#SBATCH --mail-user=!!##USERMAIL##!!
#SBATCH --signal=B:SIGQUIT@120


ORIGIN=$PWD
echo "The current dir ${PWD}"

echo "Entering !!##RUNDIR##!!"
cd !!##RUNDIR##!!

INPUT_FILE=!!##INPFILE##!!
echo "The inp file is ${INPUT_FILE}"

WORK_DIRECTORY_TO_STORE_RESULTS=$PWD
echo "The wdir is ${WORK_DIRECTORY_TO_STORE_RESULTS}"

# Optional workflow gate: a Condition node injects a check here that exits 0
# (skipping this calculation) before any modules are loaded or work is done, if
# the condition on the feeding job's output isn't met. Empty for normal jobs.
!!##PREAMBLE##!!

module purge
module load orca/6.0.1-shared

LOCAL_COMPUTE_DIRECTORY=/scratch/${SLURM_JOB_ID}
echo "The compute dir is ${LOCAL_COMPUTE_DIRECTORY}. Entering."
mkdir -p ${LOCAL_COMPUTE_DIRECTORY}

# Copy aux files back from node-local /scratch to the shared filesystem.
# Triggered on normal exit or any of the signals trapped below — including
# the SIGQUIT SLURM sends 120s before walltime expires (see --signal above).
finalize_job()
{
  echo  "function finalize_job called at `date`"
  rm --force *.tmp
  cp --archive --update --verbose \
      ${LOCAL_COMPUTE_DIRECTORY}/* \
      ${WORK_DIRECTORY_TO_STORE_RESULTS}
  rm --recursive --force ${LOCAL_COMPUTE_DIRECTORY}
}

echo "hitting trap."
trap -- 'finalize_job' USR1 SIGQUIT SIGINT SIGABRT SIGKILL \
                       SIGHUP SIGTERM EXIT TERM

echo "Copying ${INPUT_FILE} to ${LOCAL_COMPUTE_DIRECTORY}."
cp --archive --update --verbose \
      ${INPUT_FILE} \
      ${LOCAL_COMPUTE_DIRECTORY}


echo "entering local compute dir ${LOCAL_COMPUTE_DIRECTORY}"
cd ${LOCAL_COMPUTE_DIRECTORY}

# stdbuf -oL -eL forces line-buffered stdout/stderr so ORCA progress appears
# in the SLURM .out file on the shared FS in (near) real time. Without this,
# Linux block-buffers stdout and you only see output in large chunks.
echo "Starting orca: "
stdbuf -oL -eL $( which --skip-alias orca ) $( basename ${INPUT_FILE} ) &
wait

echo "Finalizing job"
finalize_job

echo "trap call"
trap - USR1 SIGQUIT SIGINT SIGABRT SIGKILL \
       SIGHUP SIGTERM EXIT TERM

echo "return to origin."
cd ${ORIGIN}
