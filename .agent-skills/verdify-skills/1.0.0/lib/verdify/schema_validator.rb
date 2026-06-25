# frozen_string_literal: true

module Verdify
  class SchemaValidator
    TYPE_CHECKS = {
      "object" => ->(v) { v.is_a?(Hash) },
      "array" => ->(v) { v.is_a?(Array) },
      "string" => ->(v) { v.is_a?(String) },
      "integer" => ->(v) { v.is_a?(Integer) && !v.is_a?(TrueClass) && !v.is_a?(FalseClass) },
      "number" => ->(v) { v.is_a?(Numeric) && !v.is_a?(TrueClass) && !v.is_a?(FalseClass) },
      "boolean" => ->(v) { v == true || v == false },
      "null" => ->(v) { v.nil? }
    }.freeze

    def self.load_document(path)
      path = Pathname.new(path)
      case path.extname.downcase
      when ".json"
        JSON.parse(path.read)
      when ".yaml", ".yml"
        YAML.safe_load(path.read, permitted_classes: [], aliases: false)
      else
        raise Error, "Unsupported artifact type: #{path}"
      end
    rescue JSON::ParserError, Psych::Exception => e
      raise Error, "Could not parse #{path}: #{e.message}"
    end

    def self.validate_file(document_path, schema_path)
      document = load_document(document_path)
      schema = load_document(schema_path)
      new.validate(document, schema)
    end

    def validate(value, schema, path = "$")
      errors = []
      return errors unless schema.is_a?(Hash)

      if schema.key?("const") && value != schema["const"]
        errors << "#{path}: expected constant #{schema['const'].inspect}, got #{value.inspect}"
      end

      if schema["enum"].is_a?(Array) && !schema["enum"].include?(value)
        errors << "#{path}: expected one of #{schema['enum'].inspect}, got #{value.inspect}"
      end

      if schema.key?("type")
        types = Array(schema["type"]).map(&:to_s)
        unless types.any? { |type| TYPE_CHECKS.fetch(type, ->(_v) { true }).call(value) }
          errors << "#{path}: expected type #{types.join(' or ')}, got #{ruby_type(value)}"
          return errors
        end
      end

      if value.is_a?(Hash)
        required = Array(schema["required"])
        required.each do |key|
          errors << "#{path}: missing required property #{key.inspect}" unless value.key?(key)
        end

        properties = schema["properties"].is_a?(Hash) ? schema["properties"] : {}
        value.each do |key, child|
          if properties.key?(key)
            errors.concat(validate(child, properties[key], "#{path}.#{key}"))
          elsif schema["additionalProperties"] == false
            errors << "#{path}: unexpected property #{key.inspect}"
          elsif schema["additionalProperties"].is_a?(Hash)
            errors.concat(validate(child, schema["additionalProperties"], "#{path}.#{key}"))
          end
        end
      end

      if value.is_a?(Array)
        if schema["minItems"] && value.length < schema["minItems"].to_i
          errors << "#{path}: expected at least #{schema['minItems']} items, got #{value.length}"
        end
        if schema["maxItems"] && value.length > schema["maxItems"].to_i
          errors << "#{path}: expected at most #{schema['maxItems']} items, got #{value.length}"
        end
        if schema["uniqueItems"] && value.uniq.length != value.length
          errors << "#{path}: items must be unique"
        end
        if schema["items"].is_a?(Hash)
          value.each_with_index { |child, index| errors.concat(validate(child, schema["items"], "#{path}[#{index}]")) }
        end
      end

      if value.is_a?(String)
        errors << "#{path}: shorter than minLength #{schema['minLength']}" if schema["minLength"] && value.length < schema["minLength"].to_i
        errors << "#{path}: longer than maxLength #{schema['maxLength']}" if schema["maxLength"] && value.length > schema["maxLength"].to_i
        if schema["pattern"]
          pattern = Regexp.new(schema["pattern"])
          errors << "#{path}: does not match #{schema['pattern'].inspect}" unless pattern.match?(value)
        end
      end

      if value.is_a?(Numeric)
        errors << "#{path}: below minimum #{schema['minimum']}" if schema.key?("minimum") && value < schema["minimum"]
        errors << "#{path}: above maximum #{schema['maximum']}" if schema.key?("maximum") && value > schema["maximum"]
      end

      if schema["allOf"].is_a?(Array)
        schema["allOf"].each { |subschema| errors.concat(validate(value, subschema, path)) }
      end

      if schema["anyOf"].is_a?(Array)
        matches = schema["anyOf"].count { |subschema| validate(value, subschema, path).empty? }
        errors << "#{path}: does not satisfy any allowed schema" if matches.zero?
      end

      if schema["oneOf"].is_a?(Array)
        matches = schema["oneOf"].count { |subschema| validate(value, subschema, path).empty? }
        errors << "#{path}: expected exactly one matching schema, got #{matches}" unless matches == 1
      end

      errors
    rescue RegexpError => e
      ["#{path}: invalid schema pattern: #{e.message}"]
    end

    private

    def ruby_type(value)
      case value
      when Hash then "object"
      when Array then "array"
      when String then "string"
      when Integer then "integer"
      when Numeric then "number"
      when TrueClass, FalseClass then "boolean"
      when NilClass then "null"
      else value.class.name
      end
    end
  end
end
