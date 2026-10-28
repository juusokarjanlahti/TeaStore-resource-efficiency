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
 *
 * Modified in 2026 by Juuso Karjanlahti: added the rendered-image response cache and its runtime switches.
 */
package tools.descartes.teastore.image;

import java.awt.image.BufferedImage;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import tools.descartes.teastore.entities.ImageSize;
import tools.descartes.teastore.entities.ImageSizePreset;
import tools.descartes.teastore.image.cache.IDataCache;
import tools.descartes.teastore.image.setup.ImageIDFactory;
import tools.descartes.teastore.image.storage.IDataStorage;

/**
 * The actual image provider class containing the mapping between products, web interface static images, the
 * cache and underlying storage. Only one instance of an image provider can exist.
 *
 * <p>In addition to the {@link tools.descartes.teastore.image.cache.IDataCache} that may sit in front of the
 * configured {@code storage} (avoiding repeated disk reads of the raw, already base64-encoded image bytes),
 * this class maintains a second, higher-level {@link RenderedImageCache}. It caches the fully rendered
 * response string returned to callers, avoiding the linear scan through the storage-level cache and the
 * repeated byte-to-string decode that would otherwise happen on every single request for an identical
 * (product or web image, size) pair, even though that pair's rendered output never changes on its own.
 * @author Norbert Schmitt
 */
public enum ImageProvider {

  /**
   * Instance of the image provider.
   */
  IP;

  /**
   * Standard image identifier if a product or web interface image cannot be found in the cache and storage.
   */
  public static final String IMAGE_NOT_FOUND = "notFound";

  /**
   * System property that can be set to {@code false} to disable the rendered-image response cache without a
   * rebuild, e.g. to run an apples-to-apples before/after comparison against the same binary.
   */
  public static final String RENDERED_CACHE_ENABLED_PROPERTY = "image.renderedCache.enabled";

  /**
   * System property that sets the byte budget of the rendered-image response cache at startup, e.g. to sweep the
   * budget against the same binary. Unlike the enable flag it is read once, when the cache is created; at runtime
   * the budget is changed through {@link #setRenderedCacheMaxSize(long)}.
   */
  public static final String RENDERED_CACHE_MAX_BYTES_PROPERTY = "image.renderedCache.maxBytes";

  private ImageDB db;
  private IDataStorage<StoreImage> storage;
  private Logger log = LoggerFactory.getLogger(ImageProvider.class);
  private final RenderedImageCache renderedImageCache = new RenderedImageCache(configuredRenderedCacheMaxBytes());

  private ImageProvider() {

  }

  /**
   * Assign the image provider the mapping between products and web interface static images.
   * @param imgDB Image database, mapping between products and web interface static images.
   */
  public void setImageDB(ImageDB imgDB) {
	if (imgDB != null) {
	  db = imgDB;
	}
  }

  /**
   * Assign the storage containing all available images. This can either be a cache or the actual hard drive storage.
   * @param imgStorage Image storage containing all available images.
   */
  public void setStorage(IDataStorage<StoreImage> imgStorage) {
	if (imgStorage != null) {
      storage = imgStorage;
	}
  }

  /**
   * Clears the rendered-image response cache and resets its hit/miss/eviction counters. Must be called
   * whenever the image database or the underlying storage is (re-)configured: a product's images are only
   * ever regenerated as a whole, under a new image id, never patched in place, and this cache is keyed by
   * product id / web image name rather than by image id, so it cannot otherwise detect that the images it
   * holds refer to now-superseded content. The counters are reset alongside the entries since a hit rate
   * measured against since-discarded content is not meaningful for the new one.
   */
  public void invalidateRenderedCache() {
    renderedImageCache.clear();
  }

  /**
   * Sets the maximum size in bytes the rendered-image response cache may use.
   * @param maxBytes Positive maximum cache size in bytes.
   * @return True if the new cache size was applied, false if the supplied size was not positive.
   */
  public boolean setRenderedCacheMaxSize(long maxBytes) {
    if (maxBytes <= 0) {
      log.info("Tried to set the rendered image cache size to a value below or equal to zero. Keeping old value.");
      return false;
    }
    renderedImageCache.setMaxBytes(maxBytes);
    return true;
  }

  /**
   * Returns the maximum size in bytes the rendered-image response cache may use.
   * @return Maximum cache size in bytes.
   */
  public long getRenderedCacheMaxSize() {
    return renderedImageCache.getMaxBytes();
  }

  /**
   * Returns the number of entries currently held by the rendered-image response cache.
   * @return Number of cached entries.
   */
  public long getRenderedCacheEntryCount() {
    return renderedImageCache.getEntryCount();
  }

  /**
   * Returns the number of bytes currently used by the rendered-image response cache.
   * @return Number of bytes currently in use.
   */
  public long getRenderedCacheByteSize() {
    return renderedImageCache.getByteSize();
  }

  /**
   * Returns the number of rendered-image response cache hits recorded since startup or the last invalidation.
   * @return Number of cache hits.
   */
  public long getRenderedCacheHitCount() {
    return renderedImageCache.getHitCount();
  }

  /**
   * Returns the number of rendered-image response cache misses recorded since startup or the last invalidation.
   * @return Number of cache misses.
   */
  public long getRenderedCacheMissCount() {
    return renderedImageCache.getMissCount();
  }

  /**
   * Returns the number of entries evicted from the rendered-image response cache since startup or the last
   * invalidation.
   * @return Number of evicted entries.
   */
  public long getRenderedCacheEvictionCount() {
    return renderedImageCache.getEvictionCount();
  }

  /**
   * Searches and returns the requested product images in the requested sizes. If an image can not be found, the
   * standard "not found" image is returned. If an image is found in the incorrect size, the largest size of this image
   * will be scaled and the scaled version will be moved to storage and returned.
   * @param images Map of product IDs and image sizes to search for.
   * @return Map between product IDs and base64 encoded image data as string.
   */
  public Map<Long, String> getProductImages(Map<Long, ImageSize> images) {
    Map<Long, String> result = new HashMap<>();
    for (Map.Entry<Long, ImageSize> entry : images.entrySet()) {
      String imgStr = getImageFor(new ImageDBKey(entry.getKey()), entry.getValue());
      if (imgStr == null) {
        continue;
      }
      result.put(entry.getKey(), imgStr);
    }
    return result;
  }

  /**
   * Searches and returns the requested web interface images in the requested sizes. If an image can not be found, the
   * standard "not found" image is returned. If an image is found in the incorrect size, the largest size of this image
   * will be scaled and the scaled version will be moved to storage and returned.
   * @param images Map of product IDs and image sizes to search for.
   * @return Map between product IDs and base64 encoded image data as string.
   */
  public Map<String, String> getWebUIImages(Map<String, ImageSize> images) {
    Map<String, String> result = new HashMap<>();
    for (Map.Entry<String, ImageSize> entry : images.entrySet()) {
      String imgStr = getImageFor(new ImageDBKey(entry.getKey()), entry.getValue());
      if (imgStr == null) {
        continue;
      }
      result.put(entry.getKey(), imgStr);
    }
    return result;
  }

  private StoreImage scaleAndRegisterImg(BufferedImage image, ImageDBKey key, ImageSize size) {
    // Product photos follow the configured format; web interface graphics stay PNG
    String format = StoreImage.STORE_IMAGE_FORMAT;
    if (key.isProductKey()) {
      format = StoreImage.productImageFormat();
    }
    StoreImage storedImg = new StoreImage(ImageIDFactory.ID.getNextImageID(),
        ImageScaler.scale(image, size), size, format);
    db.setImageMapping(key, storedImg.getId(), size);
    storage.saveData(storedImg);
    return storedImg;
  }

  private String getImageFor(ImageDBKey key, ImageSize size) {
    if (db == null || storage == null) {
      log.warn("Image provider not correctly initialized. Missing image database and storage.");
      return null;
    }
    if (key == null || size == null) {
      log.info("Supplied image key or size are null.");
      return null;
    }
    if (!key.isProductKey() && (key.getWebUIName() == null || key.getWebUIName().isEmpty())) {
      log.info("Supplied image key invalid. Is neither web image nor product image.");
      return null;
    }

    boolean cacheEnabled = isRenderedCacheEnabled();
    String cacheKey = key.toString() + "@" + size.toString();
    if (cacheEnabled) {
      String cached = renderedImageCache.get(cacheKey);
      if (cached != null) {
        return cached;
      }
    }

    ImageSize stdSize = ImageSizePreset.STD_IMAGE_SIZE;
    StoreImage storedImg = null;

    // Try to retrieve image from disk or from cache
    long imgID = db.getImageID(key, size);
    if (imgID != 0) {
      storedImg = storage.loadData(imgID);
    }

    // If we dont have the image in the right size, get the biggest one and scale it
    if (storedImg == null) {
      storedImg = storage.loadData(db.getImageID(key, stdSize));
      if (storedImg != null) {
        storedImg = scaleAndRegisterImg(storedImg.getImage(), key, size);
      } else {
        storedImg = storage.loadData(db.getImageID(IMAGE_NOT_FOUND, size));
        if (storedImg == null) {
          storedImg = storage.loadData(db.getImageID(IMAGE_NOT_FOUND, stdSize));
          if (storedImg == null) {
            return null;
          }
          storedImg = scaleAndRegisterImg(storedImg.getImage(), new ImageDBKey(IMAGE_NOT_FOUND),
              size);
        }
      }
    }

    String result = storedImg.toString();
    if (cacheEnabled) {
      renderedImageCache.put(cacheKey, result);
    }
    return result;
  }

  /**
   * Reads the rendered-image cache budget from {@link #RENDERED_CACHE_MAX_BYTES_PROPERTY}. An invalid value falls
   * back to the default rather than failing startup; the budget in effect is reported by the state endpoint.
   * @return The configured budget in bytes, or {@link IDataCache#STD_MAX_CACHE_SIZE} if the property is unset, not
   *         a number or not positive.
   */
  static long configuredRenderedCacheMaxBytes() {
    String value = System.getProperty(RENDERED_CACHE_MAX_BYTES_PROPERTY);
    if (value == null) {
      return IDataCache.STD_MAX_CACHE_SIZE;
    }
    long maxBytes;
    try {
      maxBytes = Long.parseLong(value.trim());
    } catch (NumberFormatException e) {
      maxBytes = 0;
    }
    if (maxBytes <= 0) {
      LoggerFactory.getLogger(ImageProvider.class).warn("Ignoring invalid " + RENDERED_CACHE_MAX_BYTES_PROPERTY
          + " value '" + value + "'. Using the default of " + IDataCache.STD_MAX_CACHE_SIZE + " bytes.");
      return IDataCache.STD_MAX_CACHE_SIZE;
    }
    return maxBytes;
  }

  // Read on every call rather than cached in a static field so the flag can be flipped at runtime, e.g. by
  // tests or an A/B benchmark run against the very same JVM/binary.
  private boolean isRenderedCacheEnabled() {
    return Boolean.parseBoolean(System.getProperty(RENDERED_CACHE_ENABLED_PROPERTY, "true"));
  }

  /**
   * Byte-budgeted least-recently-used cache for fully rendered (base64 data-URI) image responses. Every
   * mutating and accounting operation is {@code synchronized} on this instance, which is sufficient since the
   * REST layer serves a comparatively low, non-latency-critical number of concurrent image requests.
   * @author Norbert Schmitt
   */
  private static final class RenderedImageCache {

    private final LinkedHashMap<String, String> entries = new LinkedHashMap<>(16, 0.75f, true);
    private long maxBytes;
    private long currentBytes;
    private final AtomicLong hitCount = new AtomicLong();
    private final AtomicLong missCount = new AtomicLong();
    private final AtomicLong evictionCount = new AtomicLong();

    private RenderedImageCache(long maxBytes) {
      this.maxBytes = maxBytes;
    }

    private synchronized String get(String key) {
      String value = entries.get(key);
      if (value == null) {
        missCount.incrementAndGet();
      } else {
        hitCount.incrementAndGet();
      }
      return value;
    }

    private synchronized void put(String key, String value) {
      // Base64 data only ever uses ASCII characters, so char count equals UTF-8 byte count here.
      String old = entries.put(key, value);
      currentBytes += value.length() - (old == null ? 0 : old.length());
      evictWhileOverBudget();
    }

    private synchronized void setMaxBytes(long maxBytes) {
      this.maxBytes = maxBytes;
      evictWhileOverBudget();
    }

    private void evictWhileOverBudget() {
      Iterator<Map.Entry<String, String>> iterator = entries.entrySet().iterator();
      while (currentBytes > maxBytes && iterator.hasNext()) {
        Map.Entry<String, String> eldest = iterator.next();
        currentBytes -= eldest.getValue().length();
        iterator.remove();
        evictionCount.incrementAndGet();
      }
    }

    private synchronized void clear() {
      entries.clear();
      currentBytes = 0;
      hitCount.set(0);
      missCount.set(0);
      evictionCount.set(0);
    }

    private synchronized long getMaxBytes() {
      return maxBytes;
    }

    private synchronized long getEntryCount() {
      return entries.size();
    }

    private synchronized long getByteSize() {
      return currentBytes;
    }

    private long getHitCount() {
      return hitCount.get();
    }

    private long getMissCount() {
      return missCount.get();
    }

    private long getEvictionCount() {
      return evictionCount.get();
    }
  }

}
