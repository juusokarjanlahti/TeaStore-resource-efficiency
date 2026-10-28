/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package tools.descartes.teastore.image.benchmark;

import java.awt.image.BufferedImage;
import java.lang.management.GarbageCollectorMXBean;
import java.lang.management.ManagementFactory;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

import tools.descartes.teastore.entities.ImageSize;
import tools.descartes.teastore.image.ImageDB;
import tools.descartes.teastore.image.ImageProvider;
import tools.descartes.teastore.image.StoreImage;
import tools.descartes.teastore.image.storage.IDataStorage;

/**
 * Manual, non-CI benchmark harness comparing {@link ImageProvider} with its rendered-image response cache
 * enabled and disabled via {@link ImageProvider#RENDERED_CACHE_ENABLED_PROPERTY}, against the very same code
 * path. This class deliberately does not follow any of Surefire's default test-class name patterns
 * ({@code **}{@code /Test*.java}, {@code **}{@code /*Test.java}, {@code **}{@code /*Tests.java},
 * {@code **}{@code /*TestCase.java}), so it is never picked up by {@code mvn test}/{@code mvn clean install}
 * and cannot destabilize CI. Run it directly (e.g. from an IDE, or via {@code mvn test-compile} followed by
 * launching this class on the reactor's test classpath) whenever fresh evaluation numbers are needed.
 *
 * <p>Measurement methodology, in the absence of JMH (not used anywhere else in this Maven reactor, and not
 * worth introducing for a single benchmark class): a warm-up phase is discarded before every measured
 * configuration to let JIT tiering settle, several independent trials are run per configuration and reported
 * as median plus min/max spread rather than a single number, and JVM-wide garbage collector counts/time are
 * sampled around each trial as a free proxy for allocation/GC pressure. This is deliberately more rigorous
 * than a single {@code System.nanoTime()} loop, but it is still an in-process, single-JVM measurement, not a
 * forked, isolated JMH run — treat the numbers as indicative, not lab-grade.
 * @author Norbert Schmitt
 */
public final class RenderedImageCacheBenchmark {

  private static final int WARMUP_ITERATIONS = 5_000;
  private static final int MEASURED_ITERATIONS = 20_000;
  private static final int TRIALS = 7;
  private static final long PRODUCT_ID = 1L;
  private static final long IMAGE_ID = 101L;
  private static final ImageSize IMAGE_SIZE = new ImageSize(120, 120);

  private RenderedImageCacheBenchmark() {

  }

  /**
   * Runs the benchmark for both the disabled and enabled cache configuration and prints a short report.
   * @param args Unused.
   */
  public static void main(String[] args) {
    System.out.println("Warm-up: " + WARMUP_ITERATIONS + " iterations, measured: " + MEASURED_ITERATIONS
        + " iterations x " + TRIALS + " trials, repeated identical request for the same (product, size).");
    System.out.println();
    report("Cache DISABLED", runTrials(false));
    report("Cache ENABLED", runTrials(true));
  }

  private static double[] runTrials(boolean cacheEnabled) {
    System.setProperty(ImageProvider.RENDERED_CACHE_ENABLED_PROPERTY, String.valueOf(cacheEnabled));
    double[] millisPerIteration = new double[TRIALS];
    for (int trial = 0; trial < TRIALS; trial++) {
      millisPerIteration[trial] = runSingleTrial();
    }
    return millisPerIteration;
  }

  private static double runSingleTrial() {
    ImageDB db = new ImageDB();
    TrackingStorage storage = new TrackingStorage();
    StoreImage baseImage = new StoreImage(IMAGE_ID,
        new BufferedImage(IMAGE_SIZE.getWidth(), IMAGE_SIZE.getHeight(), BufferedImage.TYPE_INT_ARGB),
        IMAGE_SIZE);
    db.setImageMapping(PRODUCT_ID, IMAGE_ID, IMAGE_SIZE);
    storage.saveData(baseImage);

    ImageProvider.IP.setImageDB(db);
    ImageProvider.IP.setStorage(storage);
    ImageProvider.IP.invalidateRenderedCache();

    Map<Long, ImageSize> request = new HashMap<>();
    request.put(PRODUCT_ID, IMAGE_SIZE);

    for (int i = 0; i < WARMUP_ITERATIONS; i++) {
      ImageProvider.IP.getProductImages(request);
    }

    List<GarbageCollectorMXBean> gcBeans = ManagementFactory.getGarbageCollectorMXBeans();
    long gcCountBefore = totalGcCount(gcBeans);
    long gcTimeBefore = totalGcTime(gcBeans);

    long start = System.nanoTime();
    for (int i = 0; i < MEASURED_ITERATIONS; i++) {
      ImageProvider.IP.getProductImages(request);
    }
    long elapsedNanos = System.nanoTime() - start;

    long gcCollections = totalGcCount(gcBeans) - gcCountBefore;
    long gcTimeMillis = totalGcTime(gcBeans) - gcTimeBefore;
    double millisPerIteration = (elapsedNanos / 1_000_000.0) / MEASURED_ITERATIONS;

    System.out.printf("  trial: %.4f ms/iter, storage.loadData calls=%d, gcCollections=%d, gcTimeMs=%d%n",
        millisPerIteration, storage.getLoadCallCount(), gcCollections, gcTimeMillis);

    return millisPerIteration;
  }

  private static long totalGcCount(List<GarbageCollectorMXBean> beans) {
    long sum = 0;
    for (GarbageCollectorMXBean bean : beans) {
      sum += bean.getCollectionCount();
    }
    return sum;
  }

  private static long totalGcTime(List<GarbageCollectorMXBean> beans) {
    long sum = 0;
    for (GarbageCollectorMXBean bean : beans) {
      sum += bean.getCollectionTime();
    }
    return sum;
  }

  private static void report(String label, double[] millisPerIteration) {
    double[] sorted = Arrays.copyOf(millisPerIteration, millisPerIteration.length);
    Arrays.sort(sorted);
    double median = sorted[sorted.length / 2];
    System.out.printf("%s: median=%.4f ms/iter, min=%.4f ms/iter, max=%.4f ms/iter%n", label, median,
        sorted[0], sorted[sorted.length - 1]);
    System.out.println();
  }

  /**
   * Minimal in-memory {@link IDataStorage} used only to count {@code loadData} invocations for the
   * benchmark; kept local to this class rather than shared with the JUnit tests so the two remain
   * independently runnable and are free to evolve separately.
   * @author Norbert Schmitt
   */
  private static final class TrackingStorage implements IDataStorage<StoreImage> {
    private final Map<Long, StoreImage> data = new HashMap<>();
    private final AtomicLong loadCount = new AtomicLong();

    @Override
    public boolean dataExists(long id) {
      return data.containsKey(id);
    }

    @Override
    public StoreImage loadData(long id) {
      loadCount.incrementAndGet();
      return data.get(id);
    }

    @Override
    public boolean saveData(StoreImage image) {
      if (image == null) {
        return false;
      }
      data.put(image.getId(), image);
      return true;
    }

    @Override
    public boolean dataIsStorable(StoreImage image) {
      return true;
    }

    @Override
    public boolean deleteData(StoreImage image) {
      return data.remove(image.getId()) != null;
    }

    private long getLoadCallCount() {
      return loadCount.get();
    }
  }
}
