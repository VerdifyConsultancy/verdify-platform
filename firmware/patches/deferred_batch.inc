  // Fallible POD storage: allocation failure must disconnect this API peer,
  // never enter the disabled C++ exception allocator and reboot the controller.
  struct DeferredBatch {
    static constexpr uint8_t AUX_DATA_UNUSED = std::numeric_limits<uint8_t>::max();
    struct BatchItem {
      EntityBase *entity;
      uint8_t message_type;
      uint8_t estimated_size;
      uint8_t aux_data_index{AUX_DATA_UNUSED};
    };
    static_assert(std::is_trivially_copyable<BatchItem>::value);
    static constexpr size_t MAX_ITEMS = std::numeric_limits<size_t>::max() / sizeof(BatchItem);
    BatchItem *items{nullptr};
    size_t item_count{0};
    size_t capacity{0};
    uint32_t batch_start_time{0};

    DeferredBatch() = default;
    DeferredBatch(const DeferredBatch &) = delete;
    DeferredBatch &operator=(const DeferredBatch &) = delete;
    ~DeferredBatch() { ::free(items); }

    // Geometric growth preserves the existing legitimate burst capacity. There
    // is no new entity-count cap. Check both count and byte arithmetic before
    // realloc; failure leaves the original pending buffer untouched.
    bool append_(BatchItem item) {
      if (item_count == MAX_ITEMS)
        return false;
      if (item_count == capacity) {
        const size_t next = capacity == 0 ? 1 : (capacity > MAX_ITEMS / 2 ? MAX_ITEMS : capacity * 2);
        auto *grown = static_cast<BatchItem *>(::realloc(items, next * sizeof(BatchItem)));
        if (grown == nullptr)
          return false;
        items = grown;
        capacity = next;
      }
      items[item_count++] = item;
      return true;
    }

    bool add_item(EntityBase *entity, uint8_t message_type, uint8_t estimated_size,
                  uint8_t aux_data_index = AUX_DATA_UNUSED) {
#ifdef USE_EVENT
      if (message_type != EventResponse::MESSAGE_TYPE)
#endif
      {
        for (size_t i = 0; i < item_count; ++i) {
          if (items[i].entity == entity && items[i].message_type == message_type)
            return true;
        }
      }
      return append_({entity, message_type, estimated_size, aux_data_index});
    }
    bool add_item_front(EntityBase *entity, uint8_t message_type, uint8_t estimated_size) {
      if (!append_({entity, message_type, estimated_size, AUX_DATA_UNUSED}))
        return false;
      if (item_count > 1)
        std::swap(items[0], items[item_count - 1]);
      return true;
    }
    void clear() {
      item_count = 0;
      batch_start_time = 0;
    }
    void remove_front(size_t count) __attribute__((noinline)) {
      item_count -= count;
      if (item_count != 0)
        ::memmove(items, items + count, item_count * sizeof(BatchItem));
    }
    bool empty() const { return item_count == 0; }
    size_t size() const { return item_count; }
    const BatchItem &operator[](size_t index) const { return items[index]; }
    void release_buffer() {
      if (empty()) {
        ::free(items);
        items = nullptr;
        capacity = 0;
      }
    }
  };
