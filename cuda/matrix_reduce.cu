/* TODO(a.kazantsev): rewrite reduction as follows:
 *
 * Read more at: http://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#ixzz3Pdbxsl5K
__global__ void warpReduce() {
    int laneId = threadIdx.x & 0x1f;
    // Seed starting value as inverse lane ID
    int value = 31 - laneId;

    // Use XOR mode to perform butterfly reduction
    for (int i=16; i>=1; i/=2)
        value += __shfl_xor(value, i, 32);

    // "value" now contains the sum across all threads
    printf("Thread %d final value = %d\n", threadIdx.x, value);
}
*/

/// @brief Define for reduce operation on matrix rows or columns.
/// @author Kazantsev Alexey <a.kazantsev@samsung.com>
/// @details Sizes should be declared externally (values are given for example):
///          #define REDUCE_SIZE 64
///          #define A_WIDTH 10
///          #define A_HEIGHT 100500
///
///          As well as Matricies:
///          #define A err_y
///
///          And summation by columns if neccessary (otherwise summation by rows is assumed):
///          #define A_COL
///
///          size_t WorkSize[2] = {A_WIDTH * REDUCE_SIZE} or {A_HEIGHT * REDUCE_SIZE} #ifdef A_COL
///          size_t LocalSize[2] = {REDUCE_SIZE}
///
///          The result will be in (sum + AS[0]), output offset will be in bx, write it in if (tx == 0) { ... }
  __shared__ dtype AS[REDUCE_SIZE];

  int bx = blockIdx.x; // from 0 to number of resulting output elements - 1
  int tx = threadIdx.x; // from 0 to REDUCE_SIZE - 1

  dtype sum = 0;

  #ifdef A_COL
  int offs = bx + tx * A_WIDTH;
  #define ARRAY_SIZE A_HEIGHT
  #define OFFS (REDUCE_SIZE * A_WIDTH)
  #else
  int offs = bx * A_WIDTH + tx;
  #define ARRAY_SIZE A_WIDTH
  #define OFFS REDUCE_SIZE
  #endif
  for (int i = 0; i < ARRAY_SIZE / REDUCE_SIZE; i++, offs += OFFS) {
    sum += A[offs];
  }
  // Sum the remaining part
  #if (ARRAY_SIZE % REDUCE_SIZE) != 0
  if (tx < ARRAY_SIZE % REDUCE_SIZE) {
    sum += A[offs];
  }
  #endif

  AS[tx] = sum;
  // ensure all shared loaded
  __syncthreads();

  // Final summation
  sum = 0;
  int n = MIN(ARRAY_SIZE, REDUCE_SIZE);
  while (n > 1) {
    sum += (n & 1) ? AS[n - 1] : 0;
    n >>= 1;
    if (tx < n) {
      AS[tx] += AS[n + tx];
    }
    // ensure all shared summed
    __syncthreads();
  }

  #undef OFFS
  #undef ARRAY_SIZE

  // The result will be in (sum + AS[0]), output offset will be in bx, write it in if (tx == 0) { ... }

/// Define for reduce operation on matrix rows or columns ends here.
