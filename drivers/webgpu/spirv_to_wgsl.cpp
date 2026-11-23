/**************************************************************************/
/*  spirv_to_wgsl.cpp                                                     */
/**************************************************************************/
/*                         This file is part of:                          */
/*                             GODOT ENGINE                               */
/*                        https://godotengine.org                         */
/**************************************************************************/
/* Copyright (c) 2014-present Godot Engine contributors (see AUTHORS.md). */
/* Copyright (c) 2007-2014 Juan Linietsky, Ariel Manzur.                  */
/*                                                                        */
/* Permission is hereby granted, free of charge, to any person obtaining  */
/* a copy of this software and associated documentation files (the        */
/* "Software"), to deal in the Software without restriction, including    */
/* without limitation the rights to use, copy, modify, merge, publish,    */
/* distribute, sublicense, and/or sell copies of the Software, and to     */
/* permit persons to whom the Software is furnished to do so, subject to  */
/* the following conditions:                                              */
/*                                                                        */
/* The above copyright notice and this permission notice shall be         */
/* included in all copies or substantial portions of the Software.        */
/*                                                                        */
/* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,        */
/* EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF     */
/* MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. */
/* IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY   */
/* CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,   */
/* TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE      */
/* SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.                 */
/**************************************************************************/


#include "spirv_to_wgsl.h"

#include "spirv_preprocess.h"
#include "tint_wrapper.h"

#include <cstring>
#include <vector>

char *webgpu_spirv_to_wgsl(const uint8_t *p_spv, int p_size, String *r_error) {
	static const bool tint_initialized = (tint_wrapper_initialize(), true);
	(void)tint_initialized;

	if (p_size < 20 || (p_size % 4) != 0) {
		if (r_error) {
			*r_error = "invalid SPIR-V size";
		}
		return nullptr;
	}

	Vector<uint8_t> spv;
	spv.resize(p_size);
	memcpy(spv.ptrw(), p_spv, p_size);

	spv = spirv_preprocess::prepare_for_tint(spv);

	std::vector<uint32_t> words(spv.size() / 4);
	memcpy(words.data(), spv.ptr(), spv.size());

	char *error_msg = nullptr;
	char *out = tint_wrapper_spirv_to_wgsl(words.data(), words.size(), &error_msg);
	if (out == nullptr && r_error) {
		*r_error = error_msg ? String::utf8(error_msg) : String("unknown error");
	}
	if (error_msg) {
		free(error_msg);
	}
	return out;
}
