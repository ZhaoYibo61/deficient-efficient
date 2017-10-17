# teach

Student-teacher toolbox for pytorch.



To use, install pytorch then
- pip install tqdm
- pip install git+https://github.com/szagoruyko/pyinn.git@master

Note that for pyinn, stuff gets written to the home directory, which on AFS is bad and leads to errors. A workaround is (assuming you installed with conda) to modify `<CONDA_PATH>/envs/torch/cupy/cuda/lib/python2.7/site-packages/cupy/cuda/compile.py` and change the line with `_default_cache_dir`

To train a teacher:

python main.py teacher <CONV-TYPE> -t <TEACHER_CHECKPOINT> --wrn_depth <TEACHER_DEPTH> --wrn_width <TEACHER_WIDTH>

To train a student using KD:

python main.py KD <CONV-TYPE> -t <EXISTING TEACHER CHECKPOINT> -s <STUDENT CHECKPOINT> --wrn_depth <STUDENT_DEPTH> --wrn_width <STUDENT_WIDTH>
  
To train a student using AT:

python main.py AT <CONV-TYPE> -t <EXISTING TEACHER CHECKPOINT> -s <STUDENT CHECKPOINT> --wrn_depth <STUDENT_DEPTH> --wrn_width <STUDENT_WIDTH>
  
the AT method uses KD by default, so to turn it off, set alpha to 0
  
    
