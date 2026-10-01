  // Main-loop readback only. Partial sends retain the full allocated size.
  size_t allocated_bytes() const {
    size_t bytes = 0;
    for (const auto *entry : this->queue_) {
      if (entry != nullptr)
        bytes += sizeof(Entry) + entry->size;
    }
    return bytes;
  }
  size_t pending_bytes() const {
    size_t bytes = 0;
    for (const auto *entry : this->queue_) {
      if (entry != nullptr)
        bytes += entry->remaining();
    }
    return bytes;
  }
