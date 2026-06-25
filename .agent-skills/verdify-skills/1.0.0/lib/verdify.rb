# frozen_string_literal: true

require "digest"
require "fileutils"
require "json"
require "open3"
require "optparse"
require "pathname"
require "shellwords"
require "time"
require "yaml"

module Verdify
  ROOT = Pathname.new(File.expand_path("..", __dir__))
  VERSION = ROOT.join("VERSION").read.strip

  class Error < StandardError; end
  class UsageError < Error; end
  class CommandError < Error
    attr_reader :status

    def initialize(message, status: 1)
      super(message)
      @status = status
    end
  end

  def self.utc_now
    Time.now.utc.iso8601
  end

  def self.safe_load_yaml(path)
    YAML.safe_load(File.read(path), permitted_classes: [], aliases: false) || {}
  rescue Psych::Exception => e
    raise Error, "YAML parse failed for #{path}: #{e.message}"
  end

  def self.atomic_write(path, content)
    path = Pathname.new(path)
    FileUtils.mkdir_p(path.dirname)
    temp = path.dirname.join(".#{path.basename}.tmp-#{Process.pid}")
    File.write(temp, content)
    File.rename(temp, path)
  ensure
    FileUtils.rm_f(temp) if defined?(temp) && temp
  end

  def self.slug(value, max: 48)
    normalized = value.to_s.downcase.gsub(/[^a-z0-9]+/, "-").gsub(/\A-+|-+\z/, "")
    normalized = "lane" if normalized.empty?
    normalized[0, max].gsub(/-+\z/, "")
  end
end

require_relative "verdify/schema_validator"
require_relative "verdify/semantic_validator"
require_relative "verdify/git_repository"
require_relative "verdify/cli"
